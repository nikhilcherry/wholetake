/**
 * Wholetake MV3 Browser Extension — Popup UI & Page Media Detector
 *
 * Pre-populates clip URL from storage.local (pendingClipUrl) or active tab
 * DOM inspection (<video>, <audio>, <source>, og:video, canonical X/YouTube/Instagram),
 * submits clips to the configurable Verdict Service URL, displays live polling states
 * (queued -> fetching -> detecting -> labeling -> done), and renders the verdict card
 * safely using textContent.
 */
(function (root) {
  'use strict';

  const STATUS_STEPS = ['queued', 'fetching', 'detecting', 'labeling', 'done'];
  const DEFAULT_SERVICE_URL = 'http://127.0.0.1:8790';

  function getApi() {
    if (root && root.WholetakeApi) return root.WholetakeApi;
    if (typeof WholetakeApi !== 'undefined') return WholetakeApi;
    if (typeof require === 'function') {
      try {
        return require('./api.js');
      } catch (_e) {
        return null;
      }
    }
    return null;
  }

  function getExtApi(overrideExt) {
    if (overrideExt) return overrideExt;
    if (typeof chrome !== 'undefined' && chrome && (chrome.runtime || chrome.storage || chrome.tabs)) {
      return chrome;
    }
    if (typeof browser !== 'undefined' && browser && (browser.runtime || browser.storage || browser.tabs)) {
      return browser;
    }
    return null;
  }

  function getStorage(ext, keys) {
    return new Promise((resolve) => {
      const store = ext && ext.storage && ext.storage.local;
      if (!store || typeof store.get !== 'function') {
        resolve({});
        return;
      }
      try {
        let settled = false;
        const done = (val) => {
          if (!settled) {
            settled = true;
            resolve(val && typeof val === 'object' ? val : {});
          }
        };
        const ret = store.get(keys, done);
        if (ret && typeof ret.then === 'function') {
          ret.then(done).catch(() => done({}));
        }
      } catch (_e) {
        resolve({});
      }
    });
  }

  function setStorage(ext, items) {
    return new Promise((resolve) => {
      const store = ext && ext.storage && ext.storage.local;
      if (!store || typeof store.set !== 'function') {
        resolve(false);
        return;
      }
      try {
        let settled = false;
        const done = () => {
          if (!settled) {
            settled = true;
            resolve(true);
          }
        };
        const ret = store.set(items, done);
        if (ret && typeof ret.then === 'function') {
          ret.then(done).catch(() => done());
        }
      } catch (_e) {
        resolve(false);
      }
    });
  }

  /**
   * Self-contained page media detector injected via chrome.scripting.executeScript.
   * Must not depend on external closures when serialized into the target tab.
   */
  function detectMediaInPageContext(docOverride, locOverride) {
    const doc = docOverride || (typeof document !== 'undefined' ? document : null);
    const loc = locOverride || (typeof location !== 'undefined' ? location : null);
    const pageUrl = (loc && loc.href) ? String(loc.href) : '';

    const isHttp = (u) => typeof u === 'string' && /^https?:\/\//i.test(u.trim());

    if (doc && typeof doc.querySelectorAll === 'function') {
      // 1. Check <video> and <audio> elements and nested <source src>
      const mediaEls = doc.querySelectorAll('video, audio');
      for (let i = 0; i < mediaEls.length; i++) {
        const el = mediaEls[i];
        const direct = el.currentSrc || el.src || (el.getAttribute && el.getAttribute('src')) || '';
        if (isHttp(direct)) {
          return { url: direct.trim(), source: 'media' };
        }
        if (typeof el.querySelectorAll === 'function') {
          const sources = el.querySelectorAll('source');
          for (let j = 0; j < sources.length; j++) {
            const sUrl = sources[j].src || (sources[j].getAttribute && sources[j].getAttribute('src')) || '';
            if (isHttp(sUrl)) {
              return { url: sUrl.trim(), source: 'media' };
            }
          }
        }
      }

      // 2. Check OpenGraph / Twitter stream meta tags
      const metaSelectors = [
        'meta[property="og:video:secure_url"]',
        'meta[property="og:video"]',
        'meta[property="og:audio"]',
        'meta[name="twitter:player:stream"]',
      ];
      for (let i = 0; i < metaSelectors.length; i++) {
        const m = doc.querySelector ? doc.querySelector(metaSelectors[i]) : null;
        const content = m ? (m.content || (m.getAttribute && m.getAttribute('content')) || '') : '';
        if (isHttp(content)) {
          return { url: content.trim(), source: 'meta' };
        }
      }

      // 3. Check canonical link on X/Twitter, YouTube, Instagram
      const canonEl = doc.querySelector ? doc.querySelector('link[rel="canonical"]') : null;
      const canonHref = canonEl ? (canonEl.href || (canonEl.getAttribute && canonEl.getAttribute('href')) || '') : '';
      if (isHttp(canonHref) && /(x\.com|twitter\.com|youtube\.com|youtu\.be|instagram\.com)/i.test(canonHref)) {
        return { url: canonHref.trim(), source: 'canonical' };
      }

      // 4. Check permalink anchors to X status, YouTube watch/shorts, Instagram reel/p
      const links = doc.querySelectorAll('a[href]');
      for (let i = 0; i < links.length; i++) {
        const href = links[i].href || (links[i].getAttribute && links[i].getAttribute('href')) || '';
        if (
          isHttp(href) &&
          /((x\.com|twitter\.com)\/[^/]+\/status\/\d+|youtube\.com\/(watch\?v=|shorts\/)|youtu\.be\/|instagram\.com\/(reel|p)\/)/i.test(href)
        ) {
          return { url: href.trim(), source: 'social_link' };
        }
      }
    }

    return { url: isHttp(pageUrl) ? pageUrl.trim() : '', source: 'page' };
  }

  async function detectActiveTabClip(extOverride) {
    const ext = getExtApi(extOverride);
    if (!ext || !ext.tabs || typeof ext.tabs.query !== 'function') {
      return { url: '', source: 'none' };
    }

    const queryTabs = () =>
      new Promise((resolve) => {
        try {
          let settled = false;
          const done = (tabs) => {
            if (!settled) {
              settled = true;
              resolve(Array.isArray(tabs) ? tabs : []);
            }
          };
          const ret = ext.tabs.query({ active: true, currentWindow: true }, done);
          if (ret && typeof ret.then === 'function') {
            ret.then(done).catch(() => done([]));
          }
        } catch (_e) {
          resolve([]);
        }
      });

    const tabs = await queryTabs();
    const activeTab = tabs && tabs.length > 0 ? tabs[0] : null;
    const tabUrl = activeTab && typeof activeTab.url === 'string' ? activeTab.url : '';

    if (activeTab && activeTab.id != null && ext.scripting && typeof ext.scripting.executeScript === 'function') {
      try {
        const execResult = await new Promise((resolve) => {
          let settled = false;
          const done = (res) => {
            if (!settled) {
              settled = true;
              resolve(res);
            }
          };
          try {
            const ret = ext.scripting.executeScript(
              {
                target: { tabId: activeTab.id },
                func: detectMediaInPageContext,
              },
              done
            );
            if (ret && typeof ret.then === 'function') {
              ret.then(done).catch(() => done(null));
            }
          } catch (_err) {
            done(null);
          }
        });
        if (Array.isArray(execResult) && execResult[0] && execResult[0].result && execResult[0].result.url) {
          return execResult[0].result;
        }
      } catch (_e) {
        // Fall back to tab.url when executeScript is blocked on restricted pages
      }
    }

    if (/^https?:\/\//i.test(tabUrl)) {
      return { url: tabUrl, source: 'tab' };
    }
    return { url: '', source: 'none' };
  }

  function renderStatusProgress(doc, currentStatus, jobId) {
    if (!doc) return;
    const section = doc.getElementById('status-section');
    const statusText = doc.getElementById('status-text');
    const jobIdEl = doc.getElementById('status-job-id');
    const stepsContainer = doc.getElementById('status-steps');
    if (!section) return;

    if (!currentStatus) {
      section.hidden = true;
      return;
    }
    section.hidden = false;

    const normStatus = String(currentStatus).toLowerCase();
    const statusLabels = {
      queued: 'Queued for verification...',
      fetching: 'Fetching media stream...',
      detecting: 'Running Roomstamp & Witness DSP...',
      labeling: 'Labeling omitted transcript spans...',
      done: 'Verification complete',
      error: 'Verification failed',
    };
    if (statusText) {
      statusText.textContent = statusLabels[normStatus] || ('Status: ' + normStatus);
    }
    if (jobIdEl) {
      jobIdEl.textContent = jobId ? ('#' + String(jobId).slice(0, 12)) : '';
    }

    if (stepsContainer && typeof stepsContainer.querySelectorAll === 'function') {
      const stepEls = stepsContainer.querySelectorAll('[data-step]');
      const activeIdx = STATUS_STEPS.indexOf(normStatus);
      for (let i = 0; i < stepEls.length; i++) {
        const el = stepEls[i];
        const stepName = el.getAttribute('data-step');
        const stepIdx = STATUS_STEPS.indexOf(stepName);
        el.classList.remove('is-active', 'is-completed', 'is-error');
        if (normStatus === 'done') {
          el.classList.add('is-completed');
        } else if (normStatus === 'error') {
          if (stepName === 'done') {
            el.classList.add('is-error');
          }
        } else if (activeIdx >= 0) {
          if (stepIdx < activeIdx) {
            el.classList.add('is-completed');
          } else if (stepIdx === activeIdx) {
            el.classList.add('is-active');
          }
        }
      }
    }
  }

  function renderErrorBanner(doc, errorInfo) {
    if (!doc) return;
    const banner = doc.getElementById('error-banner');
    const titleEl = doc.getElementById('error-title');
    const msgEl = doc.getElementById('error-message');
    if (!banner) return;

    if (!errorInfo || (!errorInfo.error && !errorInfo.message)) {
      banner.hidden = true;
      return;
    }

    banner.hidden = false;
    const isOffline = Boolean(errorInfo.offline);
    if (titleEl) {
      titleEl.textContent = isOffline
        ? 'Verdict Service Unreachable (Offline)'
        : 'Verification Error';
    }
    if (msgEl) {
      const rawMsg = String(errorInfo.error || errorInfo.message || 'Unknown error');
      msgEl.textContent = isOffline
        ? rawMsg + ' — Check that `./py -m verdict serve` is running and the Service URL is correct.'
        : rawMsg;
    }
  }

  function formatSeconds(val) {
    if (typeof val !== 'number' || !Number.isFinite(val)) return null;
    return val.toFixed(1) + 's';
  }

  function formatScore(val) {
    if (typeof val !== 'number' || !Number.isFinite(val)) return '—';
    return val.toFixed(2);
  }

  function renderVerdictCard(doc, summary) {
    if (!doc) return;
    const card = doc.getElementById('verdict-card');
    if (!card) return;

    if (!summary || typeof summary !== 'object') {
      card.hidden = true;
      return;
    }
    card.hidden = false;

    const badgeEl = doc.getElementById('verdict-badge');
    const streamEl = doc.getElementById('verdict-stream');
    const cutsSummaryEl = doc.getElementById('cuts-summary');
    const cutsListEl = doc.getElementById('cuts-list');
    const stampBadgeEl = doc.getElementById('stamp-badge');
    const stampVenueEl = doc.getElementById('stamp-venue');
    const stampTimeEl = doc.getElementById('stamp-time');
    const stampScoresEl = doc.getElementById('stamp-scores');
    const stampCutsListEl = doc.getElementById('stamp-cuts-list');
    const proofLinkEl = doc.getElementById('proof-link');

    const verdict = String(summary.verdict || 'unknown').toLowerCase();
    const badgeLabels = {
      cut: 'CUT DETECTED',
      faithful: 'FAITHFUL (UNCUT)',
      spliced: 'SPLICED',
      not_found: 'NOT FOUND',
      unknown: 'UNKNOWN',
    };

    if (badgeEl) {
      badgeEl.className = 'wt-badge wt-badge-' + (badgeLabels[verdict] ? verdict : 'unknown');
      badgeEl.textContent = badgeLabels[verdict] || verdict.toUpperCase();
    }
    if (streamEl) {
      streamEl.textContent = summary.stream ? ('Stream: ' + String(summary.stream)) : '';
    }

    const cuts = Array.isArray(summary.cuts) ? summary.cuts : [];
    if (cutsListEl) {
      while (cutsListEl.firstChild) {
        cutsListEl.removeChild(cutsListEl.firstChild);
      }
    }
    if (cutsSummaryEl) {
      if (cuts.length > 0) {
        cutsSummaryEl.textContent = cuts.length === 1
          ? '1 omitted span detected in witness record:'
          : cuts.length + ' omitted spans detected in witness record:';
      } else if (verdict === 'faithful') {
        cutsSummaryEl.textContent = 'Complete: no words or seconds were removed.';
      } else if (verdict === 'not_found') {
        cutsSummaryEl.textContent = 'Clip did not match any witnessed stream in the archive.';
      } else {
        cutsSummaryEl.textContent = 'No omitted transcript spans reported.';
      }
    }

    if (cutsListEl && cuts.length > 0) {
      for (let i = 0; i < cuts.length; i++) {
        const c = cuts[i] || {};
        const li = doc.createElement('li');
        li.className = 'wt-cut-item';

        const parts = [];
        const atClip = formatSeconds(c.afterClipS);
        if (atClip) parts.push('At clip ' + atClip);
        if (Array.isArray(c.srcS) && c.srcS.length === 2) {
          const s0 = Number(c.srcS[0]);
          const s1 = Number(c.srcS[1]);
          if (Number.isFinite(s0) && Number.isFinite(s1)) {
            const dur = Math.max(0, s1 - s0);
            parts.push('(' + dur.toFixed(1) + 's removed from source ' + s0.toFixed(1) + 's–' + s1.toFixed(1) + 's)');
          }
        }
        const prefixText = parts.length > 0 ? (parts.join(' ') + ': ') : 'Omitted: ';
        const prefixSpan = doc.createElement('span');
        prefixSpan.textContent = prefixText;
        li.appendChild(prefixSpan);

        const wordsSpan = doc.createElement('span');
        wordsSpan.className = 'wt-cut-words';
        wordsSpan.textContent = c.words ? ('"' + String(c.words) + '"') : '(audio span)';
        li.appendChild(wordsSpan);

        cutsListEl.appendChild(li);
      }
    }

    const stamp = summary.stamp && typeof summary.stamp === 'object' ? summary.stamp : { present: false };
    if (stampBadgeEl) {
      stampBadgeEl.classList.toggle('is-present', Boolean(stamp.present));
      stampBadgeEl.textContent = stamp.present ? 'VERIFIED IN ROOM' : 'NOT PRESENT';
    }
    if (stampVenueEl) {
      stampVenueEl.textContent = stamp.venue ? String(stamp.venue) : '—';
    }
    if (stampTimeEl) {
      stampTimeEl.textContent = stamp.recordedAtUtc ? String(stamp.recordedAtUtc) : '—';
    }
    if (stampScoresEl) {
      if (stamp.layer1Score != null || stamp.layer2Score != null) {
        stampScoresEl.textContent = 'L1: ' + formatScore(stamp.layer1Score) + ' | L2: ' + formatScore(stamp.layer2Score);
      } else {
        stampScoresEl.textContent = '—';
      }
    }

    if (stampCutsListEl) {
      while (stampCutsListEl.firstChild) {
        stampCutsListEl.removeChild(stampCutsListEl.firstChild);
      }
      const sCuts = Array.isArray(stamp.cuts) ? stamp.cuts : [];
      for (let i = 0; i < sCuts.length; i++) {
        const sc = sCuts[i] || {};
        const li = doc.createElement('li');
        li.className = 'wt-cut-item';
        const atS = formatSeconds(sc.at_clip_s != null ? sc.at_clip_s : sc.atClipS);
        const remS = formatSeconds(sc.removed_s != null ? sc.removed_s : sc.removedS);
        li.textContent = 'Watermark phase cut at ' + (atS || '?') + ' (' + (remS || '?') + ' removed)';
        stampCutsListEl.appendChild(li);
      }
    }

    if (proofLinkEl) {
      const rawProofUrl = String(summary.proofUrl || '').trim();
      if (/^https?:\/\//i.test(rawProofUrl)) {
        proofLinkEl.href = rawProofUrl;
        proofLinkEl.hidden = false;
      } else {
        proofLinkEl.href = '#';
        proofLinkEl.hidden = true;
      }
    }
  }

  async function startClipCheck(options) {
    const opts = options || {};
    const doc = opts.doc || (typeof document !== 'undefined' ? document : null);
    const ext = getExtApi(opts.ext);
    const api = getApi();
    if (!api) {
      renderErrorBanner(doc, { error: 'Wholetake API helper (api.js) is not loaded.' });
      return { ok: false, error: 'api.js not loaded' };
    }

    const serviceInput = doc ? doc.getElementById('service-url-input') : null;
    const clipInput = doc ? doc.getElementById('clip-url-input') : null;
    const checkBtn = doc ? doc.getElementById('check-clip-btn') : null;

    const rawServiceUrl = opts.serviceUrl || (serviceInput ? serviceInput.value : DEFAULT_SERVICE_URL);
    const serviceUrl = api.normalizeServiceUrl(rawServiceUrl);
    if (serviceInput) serviceInput.value = serviceUrl;

    const clipUrl = String(opts.clipUrl != null ? opts.clipUrl : (clipInput ? clipInput.value : '')).trim();
    if (!clipUrl || !/^https?:\/\//i.test(clipUrl)) {
      const msg = 'Please enter a valid http:// or https:// clip URL.';
      renderErrorBanner(doc, { error: msg, offline: false });
      return { ok: false, error: msg };
    }

    await setStorage(ext, {
      serviceUrl: serviceUrl,
      pendingClipUrl: clipUrl,
      updatedAt: Date.now(),
    });

    if (checkBtn) checkBtn.disabled = true;
    renderErrorBanner(doc, null);
    renderVerdictCard(doc, null);
    renderStatusProgress(doc, 'queued', null);

    try {
      const sub = await api.submitClip(serviceUrl, clipUrl, {
        fetchImpl: opts.fetchImpl,
        timeoutMs: opts.submitTimeoutMs,
      });

      if (!sub.ok) {
        renderStatusProgress(doc, 'error', null);
        renderErrorBanner(doc, {
          error: sub.error || 'Failed to submit clip to Verdict Service',
          offline: Boolean(sub.offline),
        });
        return sub;
      }

      const jobId = sub.id;
      renderStatusProgress(doc, sub.jobStatus || 'queued', jobId);

      const polled = await api.pollVerdict(serviceUrl, jobId, {
        fetchImpl: opts.fetchImpl,
        intervalMs: opts.intervalMs,
        timeoutMs: opts.pollTimeoutMs,
        onProgress: (jobSnap) => {
          if (jobSnap && jobSnap.status) {
            renderStatusProgress(doc, jobSnap.status, jobId);
          }
          if (typeof opts.onProgress === 'function') {
            opts.onProgress(jobSnap);
          }
        },
      });

      if (!polled.ok) {
        renderStatusProgress(doc, 'error', jobId);
        renderErrorBanner(doc, {
          error: polled.error || 'Verdict check failed',
          offline: Boolean(polled.offline),
        });
        return polled;
      }

      renderStatusProgress(doc, 'done', jobId);
      const summary = api.formatVerdictSummary(polled.job, serviceUrl);
      renderVerdictCard(doc, summary);

      await setStorage(ext, {
        activeJob: polled.job,
        lastSummary: summary,
        lastError: null,
        updatedAt: Date.now(),
      });

      return { ok: true, job: polled.job, summary: summary };
    } finally {
      if (checkBtn) checkBtn.disabled = false;
    }
  }

  async function initPopup(options) {
    const opts = options || {};
    const doc = opts.doc || (typeof document !== 'undefined' ? document : null);
    const ext = getExtApi(opts.ext);
    const api = getApi();
    if (!doc) return null;

    const serviceInput = doc.getElementById('service-url-input');
    const saveServiceBtn = doc.getElementById('save-service-url-btn');
    const savedHint = doc.getElementById('service-url-saved');
    const clipInput = doc.getElementById('clip-url-input');
    const clipHint = doc.getElementById('clip-source-hint');
    const detectBtn = doc.getElementById('detect-clip-btn');
    const checkBtn = doc.getElementById('check-clip-btn');

    const stored = await getStorage(ext, [
      'serviceUrl',
      'pendingClipUrl',
      'lastCandidate',
      'activeJob',
      'lastSummary',
      'lastError',
    ]);

    const serviceUrl = api
      ? api.normalizeServiceUrl(stored.serviceUrl || DEFAULT_SERVICE_URL)
      : (stored.serviceUrl || DEFAULT_SERVICE_URL);
    if (serviceInput) {
      serviceInput.value = serviceUrl;
    }

    let clipUrl = stored.pendingClipUrl || '';
    let clipSource = (stored.lastCandidate && stored.lastCandidate.source) || '';

    if (!clipUrl) {
      const detected = await detectActiveTabClip(ext);
      if (detected && detected.url) {
        clipUrl = detected.url;
        clipSource = detected.source || 'tab';
      }
    }

    if (clipInput && clipUrl) {
      clipInput.value = clipUrl;
    }
    if (clipHint && clipUrl) {
      clipHint.textContent = clipSource ? ('Detected from ' + clipSource) : '';
    }

    if (stored.lastSummary && stored.activeJob && stored.activeJob.status === 'done') {
      renderStatusProgress(doc, 'done', stored.activeJob.id);
      renderVerdictCard(doc, stored.lastSummary);
    }

    if (saveServiceBtn && serviceInput) {
      saveServiceBtn.addEventListener('click', async () => {
        const norm = api ? api.normalizeServiceUrl(serviceInput.value) : serviceInput.value.trim();
        serviceInput.value = norm;
        await setStorage(ext, { serviceUrl: norm, updatedAt: Date.now() });
        if (savedHint) {
          savedHint.hidden = false;
          setTimeout(() => {
            savedHint.hidden = true;
          }, 1500);
        }
      });
    }

    if (detectBtn && clipInput) {
      detectBtn.addEventListener('click', async () => {
        const detected = await detectActiveTabClip(ext);
        if (detected && detected.url) {
          clipInput.value = detected.url;
          if (clipHint) {
            clipHint.textContent = 'Detected from ' + (detected.source || 'tab');
          }
        }
      });
    }

    if (checkBtn) {
      checkBtn.addEventListener('click', () => {
        startClipCheck({ doc: doc, ext: ext, fetchImpl: opts.fetchImpl, intervalMs: opts.intervalMs });
      });
    }

    return {
      serviceUrl: serviceUrl,
      clipUrl: clipUrl,
      clipSource: clipSource,
    };
  }

  if (typeof document !== 'undefined' && document.readyState !== 'loading') {
    if (document.getElementById('check-clip-btn')) {
      initPopup();
    }
  } else if (typeof document !== 'undefined' && typeof document.addEventListener === 'function') {
    document.addEventListener('DOMContentLoaded', () => {
      if (document.getElementById('check-clip-btn')) {
        initPopup();
      }
    });
  }

  const exported = {
    STATUS_STEPS: STATUS_STEPS,
    DEFAULT_SERVICE_URL: DEFAULT_SERVICE_URL,
    detectMediaInPageContext: detectMediaInPageContext,
    detectActiveTabClip: detectActiveTabClip,
    renderStatusProgress: renderStatusProgress,
    renderErrorBanner: renderErrorBanner,
    renderVerdictCard: renderVerdictCard,
    startClipCheck: startClipCheck,
    initPopup: initPopup,
  };

  if (typeof module !== 'undefined' && module.exports) {
    module.exports = exported;
  }
  if (root) {
    root.WholetakePopup = exported;
  }
})(typeof globalThis !== 'undefined' ? globalThis : typeof self !== 'undefined' ? self : this);
