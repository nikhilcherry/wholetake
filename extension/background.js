/**
 * Wholetake MV3 Browser Extension — Background Service Worker
 *
 * Registers the "Check this clip with Wholetake" context menu across
 * video, audio, link, and page contexts, extracts candidate clip URLs,
 * persists state to storage.local, and handles popup runtime messages.
 */
(function (root) {
  'use strict';

  if (typeof importScripts === 'function') {
    try {
      importScripts('api.js');
    } catch (_err) {
      // Ignore when loaded outside service worker or already loaded
    }
  }

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

  const MENU_ID = 'wholetake-check-clip';
  const MENU_TITLE = 'Check this clip with Wholetake';
  const MENU_CONTEXTS = ['video', 'audio', 'link', 'page'];
  const DEFAULT_SERVICE_URL = 'http://127.0.0.1:8790';

  function getExtApi(overrideExt) {
    if (overrideExt) return overrideExt;
    if (typeof chrome !== 'undefined' && chrome && (chrome.runtime || chrome.storage || chrome.contextMenus)) {
      return chrome;
    }
    if (typeof browser !== 'undefined' && browser && (browser.runtime || browser.storage || browser.contextMenus)) {
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

  function registerContextMenus(extOverride) {
    const ext = getExtApi(extOverride);
    if (!ext || !ext.contextMenus || typeof ext.contextMenus.create !== 'function') {
      return false;
    }
    const createItem = () => {
      try {
        ext.contextMenus.create(
          {
            id: MENU_ID,
            title: MENU_TITLE,
            contexts: MENU_CONTEXTS,
          },
          () => {
            if (ext.runtime && ext.runtime.lastError) {
              // Ignore duplicate menu ID error
            }
          }
        );
      } catch (_e) {
        // Ignore synchronous errors in restricted environments
      }
    };
    if (typeof ext.contextMenus.removeAll === 'function') {
      try {
        const maybePromise = ext.contextMenus.removeAll(() => createItem());
        if (maybePromise && typeof maybePromise.then === 'function') {
          maybePromise.then(() => createItem()).catch(() => createItem());
        }
        return true;
      } catch (_e) {
        // Fallback to direct create
      }
    }
    createItem();
    return true;
  }

  function extractClipFromClick(info, tab) {
    const api = getApi();
    const safeInfo = info && typeof info === 'object' ? info : {};
    const safeTab = tab && typeof tab === 'object' ? tab : {};
    const pageUrl = safeInfo.pageUrl || safeTab.url || '';

    if (api && typeof api.extractClipCandidate === 'function') {
      const candidate = api.extractClipCandidate({
        srcUrl: safeInfo.srcUrl || '',
        linkUrl: safeInfo.linkUrl || '',
        pageUrl: pageUrl,
      });
      if (candidate && candidate.url) {
        return candidate;
      }
    }

    const fallbackUrl = String(safeInfo.srcUrl || safeInfo.linkUrl || pageUrl || '').trim();
    return {
      url: fallbackUrl,
      source: safeInfo.srcUrl ? 'media' : safeInfo.linkUrl ? 'link' : pageUrl ? 'page' : 'none',
      isKnownMediaOrSocial: Boolean(fallbackUrl),
    };
  }

  async function runCheckClip(clipUrl, options) {
    const opts = options || {};
    const ext = getExtApi(opts.ext);
    const api = getApi();
    if (!api) {
      return { ok: false, offline: false, status: 0, error: 'Wholetake API helper not loaded' };
    }

    const stored = await getStorage(ext, ['serviceUrl']);
    const serviceUrl = api.normalizeServiceUrl(opts.serviceUrl || stored.serviceUrl || DEFAULT_SERVICE_URL);
    const targetUrl = String(clipUrl || '').trim();

    if (!targetUrl) {
      const errRes = { ok: false, offline: false, status: 0, error: 'Clip URL is empty' };
      await setStorage(ext, { lastError: errRes.error, updatedAt: Date.now() });
      return errRes;
    }

    const initialJob = {
      id: null,
      url: targetUrl,
      status: 'queued',
      serviceUrl: serviceUrl,
      updatedAt: Date.now(),
    };

    await setStorage(ext, {
      serviceUrl: serviceUrl,
      pendingClipUrl: targetUrl,
      activeJob: initialJob,
      lastError: null,
      updatedAt: Date.now(),
    });

    const sub = await api.submitClip(serviceUrl, targetUrl, {
      fetchImpl: opts.fetchImpl,
      timeoutMs: opts.submitTimeoutMs,
    });

    if (!sub.ok) {
      const failedJob = {
        id: null,
        url: targetUrl,
        status: 'error',
        error: sub.error || 'Failed to submit clip',
        offline: Boolean(sub.offline),
        httpStatus: sub.status || 0,
        serviceUrl: serviceUrl,
        updatedAt: Date.now(),
      };
      await setStorage(ext, {
        activeJob: failedJob,
        lastError: failedJob.error,
        updatedAt: Date.now(),
      });
      return {
        ok: false,
        offline: Boolean(sub.offline),
        status: sub.status || 0,
        error: failedJob.error,
        job: failedJob,
      };
    }

    const jobId = sub.id;
    const queuedJob = {
      id: jobId,
      url: targetUrl,
      status: sub.jobStatus || 'queued',
      serviceUrl: serviceUrl,
      updatedAt: Date.now(),
    };
    await setStorage(ext, {
      activeJob: queuedJob,
      updatedAt: Date.now(),
    });

    const polled = await api.pollVerdict(serviceUrl, jobId, {
      fetchImpl: opts.fetchImpl,
      intervalMs: opts.intervalMs,
      timeoutMs: opts.pollTimeoutMs,
      onProgress: async (jobSnap) => {
        if (jobSnap && typeof jobSnap === 'object') {
          const progressJob = Object.assign({}, jobSnap, { serviceUrl: serviceUrl, updatedAt: Date.now() });
          await setStorage(ext, {
            activeJob: progressJob,
            updatedAt: Date.now(),
          });
          if (typeof opts.onProgress === 'function') {
            try {
              opts.onProgress(progressJob);
            } catch (_e) {
              // Ignore callback errors
            }
          }
        }
      },
    });

    if (!polled.ok) {
      const errJob = Object.assign(
        {
          id: jobId,
          url: targetUrl,
          status: 'error',
          error: polled.error || 'Polling failed',
          offline: Boolean(polled.offline),
          serviceUrl: serviceUrl,
          updatedAt: Date.now(),
        },
        polled.job || {}
      );
      const errSummary = api.formatVerdictSummary(errJob, serviceUrl);
      await setStorage(ext, {
        activeJob: errJob,
        lastSummary: errSummary,
        lastError: polled.error || errJob.error || 'Polling failed',
        updatedAt: Date.now(),
      });
      return {
        ok: false,
        offline: Boolean(polled.offline),
        status: polled.status || 0,
        error: polled.error || errJob.error || 'Polling failed',
        job: errJob,
        summary: errSummary,
      };
    }

    const finalJob = Object.assign({}, polled.job, { serviceUrl: serviceUrl, updatedAt: Date.now() });
    const summary = api.formatVerdictSummary(finalJob, serviceUrl);
    await setStorage(ext, {
      activeJob: finalJob,
      lastSummary: summary,
      lastError: null,
      updatedAt: Date.now(),
    });

    return {
      ok: true,
      offline: false,
      status: 200,
      job: finalJob,
      summary: summary,
    };
  }

  async function handleContextMenuClick(info, tab, options) {
    const opts = options || {};
    const ext = getExtApi(opts.ext);
    if (!info || info.menuItemId !== MENU_ID) {
      return { handled: false };
    }

    const candidate = extractClipFromClick(info, tab);
    const pendingClipUrl = candidate.url || (tab && tab.url) || '';
    const updatedAt = Date.now();

    await setStorage(ext, {
      pendingClipUrl: pendingClipUrl,
      lastCandidate: candidate,
      updatedAt: updatedAt,
    });

    if (ext && ext.action && typeof ext.action.openPopup === 'function') {
      try {
        const p = ext.action.openPopup();
        if (p && typeof p.catch === 'function') p.catch(() => {});
      } catch (_e) {
        // openPopup may not be supported or allowed outside active window
      }
    }

    if (ext && ext.runtime && typeof ext.runtime.sendMessage === 'function') {
      try {
        const m = ext.runtime.sendMessage({
          type: 'WT_CLIP_SELECTED',
          pendingClipUrl: pendingClipUrl,
          lastCandidate: candidate,
          updatedAt: updatedAt,
        });
        if (m && typeof m.catch === 'function') m.catch(() => {});
      } catch (_e) {
        // Ignore when popup is not open
      }
    }

    let checkResult = null;
    if (opts.autoCheck && pendingClipUrl) {
      checkResult = await runCheckClip(pendingClipUrl, opts);
    }

    return {
      handled: true,
      pendingClipUrl: pendingClipUrl,
      lastCandidate: candidate,
      updatedAt: updatedAt,
      checkResult: checkResult,
    };
  }

  async function handleRuntimeMessage(message, _sender, options) {
    const opts = options || {};
    const ext = getExtApi(opts.ext);
    const api = getApi();
    if (!message || typeof message !== 'object') {
      return { ok: false, error: 'Invalid message' };
    }

    const action = message.type || message.action || '';

    if (action === 'WT_GET_STATE') {
      const state = await getStorage(ext, [
        'serviceUrl',
        'pendingClipUrl',
        'lastCandidate',
        'activeJob',
        'lastSummary',
        'lastError',
        'updatedAt',
      ]);
      const normServiceUrl = api
        ? api.normalizeServiceUrl(state.serviceUrl || DEFAULT_SERVICE_URL)
        : state.serviceUrl || DEFAULT_SERVICE_URL;
      return {
        ok: true,
        state: {
          serviceUrl: normServiceUrl,
          pendingClipUrl: state.pendingClipUrl || '',
          lastCandidate: state.lastCandidate || null,
          activeJob: state.activeJob || null,
          lastSummary: state.lastSummary || null,
          lastError: state.lastError || null,
          updatedAt: state.updatedAt || null,
        },
      };
    }

    if (action === 'WT_CHECK_CLIP') {
      const clipUrl = String(message.clipUrl || message.url || '').trim();
      const serviceUrl = message.serviceUrl;
      return await runCheckClip(clipUrl, {
        ext: ext,
        serviceUrl: serviceUrl,
        fetchImpl: opts.fetchImpl,
        intervalMs: message.intervalMs || opts.intervalMs,
        pollTimeoutMs: message.pollTimeoutMs || opts.pollTimeoutMs,
      });
    }

    return { ok: false, error: 'Unknown action: ' + String(action) };
  }

  function initBackground(extOverride) {
    const ext = getExtApi(extOverride);
    if (!ext) return false;

    if (ext.runtime && ext.runtime.onInstalled && typeof ext.runtime.onInstalled.addListener === 'function') {
      ext.runtime.onInstalled.addListener(() => {
        registerContextMenus(ext);
      });
    }

    if (ext.runtime && ext.runtime.onStartup && typeof ext.runtime.onStartup.addListener === 'function') {
      ext.runtime.onStartup.addListener(() => {
        registerContextMenus(ext);
      });
    }

    registerContextMenus(ext);

    if (ext.contextMenus && ext.contextMenus.onClicked && typeof ext.contextMenus.onClicked.addListener === 'function') {
      ext.contextMenus.onClicked.addListener((info, tab) => {
        handleContextMenuClick(info, tab, { ext: ext });
      });
    }

    if (ext.runtime && ext.runtime.onMessage && typeof ext.runtime.onMessage.addListener === 'function') {
      ext.runtime.onMessage.addListener((message, sender, sendResponse) => {
        handleRuntimeMessage(message, sender, { ext: ext })
          .then((res) => {
            if (typeof sendResponse === 'function') sendResponse(res);
          })
          .catch((err) => {
            if (typeof sendResponse === 'function') {
              sendResponse({ ok: false, error: String((err && err.message) || err || 'Background error') });
            }
          });
        return true;
      });
    }

    return true;
  }

  initBackground();

  const exported = {
    MENU_ID: MENU_ID,
    MENU_TITLE: MENU_TITLE,
    MENU_CONTEXTS: MENU_CONTEXTS,
    DEFAULT_SERVICE_URL: DEFAULT_SERVICE_URL,
    registerContextMenus: registerContextMenus,
    extractClipFromClick: extractClipFromClick,
    handleContextMenuClick: handleContextMenuClick,
    handleRuntimeMessage: handleRuntimeMessage,
    runCheckClip: runCheckClip,
    initBackground: initBackground,
  };

  if (typeof module !== 'undefined' && module.exports) {
    module.exports = exported;
  }
  if (root) {
    root.WholetakeBackground = exported;
  }
})(typeof globalThis !== 'undefined' ? globalThis : typeof self !== 'undefined' ? self : this);
