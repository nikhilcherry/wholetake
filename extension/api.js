(function (root, factory) {
  if (typeof module === "object" && module.exports) {
    module.exports = factory();
  } else {
    root.WholetakeApi = factory();
  }
})(typeof globalThis !== "undefined" ? globalThis : this, function () {
  "use strict";

  var DEFAULT_SERVICE_URL = "http://127.0.0.1:8790";

  var MEDIA_EXT_RE = /\.(mp4|m4a|mp3|wav|webm|ogg|opus|aac|flac|mov|mkv)(\?|#|$)/i;
  var SOCIAL_HOST_PATTERNS = [
    /(^|\.)(x\.com|twitter\.com)$/i,
    /(^|\.)(youtube\.com|youtu\.be)$/i,
    /(^|\.)(instagram\.com|instagr\.am)$/i,
    /(^|\.)(tiktok\.com|fb\.watch|facebook\.com|reddit\.com|t\.me)$/i
  ];

  function normalizeServiceUrl(rawUrl) {
    var str = typeof rawUrl === "string" ? rawUrl.trim() : "";
    if (!str) {
      return DEFAULT_SERVICE_URL;
    }
    if (!/^[a-zA-Z][a-zA-Z0-9+.-]*:\/\//.test(str)) {
      str = "http://" + str;
    }
    try {
      var parsed = new URL(str);
      if (parsed.protocol !== "http:" && parsed.protocol !== "https:") {
        return DEFAULT_SERVICE_URL;
      }
      var cleanPath = parsed.pathname.replace(/\/+$/, "");
      return parsed.origin + cleanPath;
    } catch (_err) {
      return DEFAULT_SERVICE_URL;
    }
  }

  function isSupportedHttpUrl(raw) {
    if (typeof raw !== "string" || !raw.trim()) {
      return false;
    }
    try {
      var u = new URL(raw.trim());
      return u.protocol === "http:" || u.protocol === "https:";
    } catch (_err) {
      return false;
    }
  }

  function isMediaOrSocialUrl(raw) {
    if (!isSupportedHttpUrl(raw)) {
      return false;
    }
    try {
      var u = new URL(raw.trim());
      if (MEDIA_EXT_RE.test(u.pathname)) {
        return true;
      }
      for (var i = 0; i < SOCIAL_HOST_PATTERNS.length; i++) {
        if (SOCIAL_HOST_PATTERNS[i].test(u.hostname)) {
          return true;
        }
      }
      return false;
    } catch (_err) {
      return false;
    }
  }

  function extractClipCandidate(context) {
    var ctx = context && typeof context === "object" ? context : {};
    var candidates = [
      { url: ctx.srcUrl, source: "media" },
      { url: ctx.linkUrl, source: "link" },
      { url: ctx.selectionText, source: "selection" },
      { url: ctx.mediaUrl, source: "dom_media" },
      { url: ctx.canonicalUrl, source: "canonical" },
      { url: ctx.pageUrl, source: "page" }
    ];

    for (var i = 0; i < candidates.length; i++) {
      var item = candidates[i];
      if (typeof item.url === "string") {
        var trimmed = item.url.trim();
        if (isSupportedHttpUrl(trimmed)) {
          return {
            url: trimmed,
            source: item.source,
            isKnownMediaOrSocial: isMediaOrSocialUrl(trimmed)
          };
        }
      }
    }
    return { url: null, source: null, isKnownMediaOrSocial: false };
  }

  function makeNetworkError(err, baseUrl) {
    var msg = err && err.message ? String(err.message) : String(err || "Network error");
    return {
      ok: false,
      offline: true,
      status: 0,
      error: "Cannot reach Wholetake service at " + baseUrl + " (" + msg + ")"
    };
  }

  function _createTimedFetch(baseFetch, timeoutMs) {
  const ms = Number(timeoutMs) > 0 ? Number(timeoutMs) : 10000;
  return async function timedFetch(url, init) {
    const controller = typeof AbortController !== 'undefined' ? new AbortController() : null;
    const fetchInit = Object.assign({}, init || {});
    if (controller && !fetchInit.signal) {
      fetchInit.signal = controller.signal;
    }
    let timer = null;
    const timeoutPromise = new Promise((_, reject) => {
      timer = setTimeout(() => {
        if (controller) {
          try { controller.abort(); } catch (_) {}
        }
        const err = new Error('Request timed out after ' + ms + 'ms');
        err.name = 'AbortError';
        err.code = 'ETIMEDOUT';
        reject(err);
      }, ms);
    });
    try {
      const resp = await Promise.race([baseFetch(url, fetchInit), timeoutPromise]);
      if (resp && typeof resp === 'object') {
        const origJson = typeof resp.json === 'function' ? resp.json.bind(resp) : null;
        const origText = typeof resp.text === 'function' ? resp.text.bind(resp) : null;
        if (origJson) {
          resp.json = () => Promise.race([origJson(), timeoutPromise]).finally(() => clearTimeout(timer));
        }
        if (origText) {
          resp.text = () => Promise.race([origText(), timeoutPromise]).finally(() => clearTimeout(timer));
        }
      }
      return resp;
    } finally {
      clearTimeout(timer);
    }
  };
}

async function submitClip(serviceUrl, clipUrl, options = {}) {
  const opts = options && typeof options === 'object' ? options : {};
  const reqTimeoutMs = Number(opts.requestTimeoutMs || opts.timeoutMs) > 0
    ? Number(opts.requestTimeoutMs || opts.timeoutMs)
    : 10000;
  const baseFetch = opts.fetchImpl || (typeof fetch !== 'undefined' ? fetch : null);
  if (typeof baseFetch !== 'function') {
    return { ok: false, offline: true, error: 'fetch is not available in this runtime' };
  }
  const timedFetch = _createTimedFetch(baseFetch, reqTimeoutMs);
  let timer = null;
  try {
    return await Promise.race([
      _origSubmitClip(serviceUrl, clipUrl, Object.assign({}, opts, { fetchImpl: timedFetch })),
      new Promise((resolve) => {
        timer = setTimeout(() => {
          resolve({ ok: false, offline: true, timeout: true, error: 'Request timed out after ' + reqTimeoutMs + 'ms' });
        }, reqTimeoutMs + 50);
      })
    ]);
  } catch (err) {
    return { ok: false, offline: true, error: err && err.message ? err.message : String(err) };
  } finally {
    clearTimeout(timer);
  }
}

async function _origSubmitClip(baseUrl, clipUrl, options) {
    var opts = options || {};
    var fetchImpl = opts.fetchImpl || (typeof fetch !== "undefined" ? fetch : null);
    var base = normalizeServiceUrl(baseUrl);
    var trimmedClip = typeof clipUrl === "string" ? clipUrl.trim() : "";
    if (!trimmedClip || !isSupportedHttpUrl(trimmedClip)) {
      return {
        ok: false,
        offline: false,
        status: 400,
        error: "Please provide a valid http:// or https:// clip URL."
      };
    }
    if (typeof fetchImpl !== "function") {
      return {
        ok: false,
        offline: true,
        status: 0,
        error: "Fetch API is unavailable in this environment."
      };
    }

    var endpoint = base + "/api/verdict";
    var response;
    try {
      response = await fetchImpl(endpoint, {
        method: "POST",
        headers: {
          "Content-Type": "application/json"
        },
        body: JSON.stringify({ url: trimmedClip })
      });
    } catch (err) {
      return makeNetworkError(err, base);
    }

    var payload = null;
    try {
      payload = await response.json();
    } catch (_err) {
      payload = null;
    }

    if (!response.ok) {
      var errText = (payload && payload.error) ? String(payload.error) : ("HTTP " + response.status);
      return {
        ok: false,
        offline: false,
        status: response.status,
        error: errText,
        data: payload
      };
    }

    if (!payload || typeof payload.id !== "string") {
      return {
        ok: false,
        offline: false,
        status: response.status,
        error: "Malformed response from Wholetake service (missing job id)."
      };
    }

    return {
      ok: true,
      offline: false,
      status: response.status,
      id: payload.id,
      jobStatus: payload.status || "queued",
      data: payload
    };
  }

  async function getVerdict(serviceUrl, jobId, options) {
  return _getVerdictInner(serviceUrl, jobId, options).then((res) => {
    if (res && res.ok && (!res.job || typeof res.job !== 'object' || Array.isArray(res.job))) {
      return {
        ok: false,
        offline: false,
        status: typeof res.status === 'number' ? res.status : 200,
        error: 'Invalid verdict response payload',
      };
    }
    return res;
  });
}

async function _getVerdictInner(serviceUrl, jobId, options = {}) {
  const opts = options && typeof options === 'object' ? options : {};
  const reqTimeoutMs = Number(opts.requestTimeoutMs || opts.timeoutMs) > 0
    ? Number(opts.requestTimeoutMs || opts.timeoutMs)
    : 10000;
  const baseFetch = opts.fetchImpl || (typeof fetch !== 'undefined' ? fetch : null);
  if (typeof baseFetch !== 'function') {
    return { ok: false, offline: true, error: 'fetch is not available in this runtime' };
  }
  const timedFetch = _createTimedFetch(baseFetch, reqTimeoutMs);
  let timer = null;
  try {
    return await Promise.race([
      _origGetVerdict(serviceUrl, jobId, Object.assign({}, opts, { fetchImpl: timedFetch })),
      new Promise((resolve) => {
        timer = setTimeout(() => {
          resolve({ ok: false, offline: true, timeout: true, error: 'Request timed out after ' + reqTimeoutMs + 'ms' });
        }, reqTimeoutMs + 50);
      })
    ]);
  } catch (err) {
    return { ok: false, offline: true, error: err && err.message ? err.message : String(err) };
  } finally {
    clearTimeout(timer);
  }
}

async function _origGetVerdict(baseUrl, jobId, options) {
    var opts = options || {};
    var fetchImpl = opts.fetchImpl || (typeof fetch !== "undefined" ? fetch : null);
    var base = normalizeServiceUrl(baseUrl);
    var cleanId = typeof jobId === "string" ? jobId.trim() : "";
    if (!cleanId) {
      return {
        ok: false,
        offline: false,
        status: 400,
        error: "Missing verdict job id."
      };
    }
    if (typeof fetchImpl !== "function") {
      return {
        ok: false,
        offline: true,
        status: 0,
        error: "Fetch API is unavailable in this environment."
      };
    }

    var endpoint = base + "/api/verdict/" + encodeURIComponent(cleanId);
    var response;
    try {
      response = await fetchImpl(endpoint, {
        method: "GET",
        headers: {
          "Accept": "application/json"
        }
      });
    } catch (err) {
      return makeNetworkError(err, base);
    }

    var payload = null;
    try {
      payload = await response.json();
    } catch (_err) {
      payload = null;
    }

    if (!response.ok) {
      var errText = (payload && payload.error) ? String(payload.error) : ("HTTP " + response.status);
      return {
        ok: false,
        offline: false,
        status: response.status,
        error: errText,
        data: payload
      };
    }

    if (!payload || typeof payload !== "object") {
      return {
        ok: false,
        offline: false,
        status: response.status,
        error: "Malformed JSON response from Wholetake service."
      };
    }

    return {
      ok: true,
      offline: false,
      status: response.status,
      job: payload,
      proofUrl: base + "/v/" + encodeURIComponent(payload.id || cleanId)
    };
  }

  function sleep(ms) {
    return new Promise(function (resolve) {
      setTimeout(resolve, ms);
    });
  }

  async function pollVerdict(serviceUrl, jobId, options) {
    const rawOpts = options && typeof options === "object" ? options : {};
    const rawInterval =
      rawOpts.intervalMs !== undefined && rawOpts.intervalMs !== null && rawOpts.intervalMs !== ""
        ? Number(rawOpts.intervalMs)
        : NaN;
    const rawTimeout =
      rawOpts.timeoutMs !== undefined && rawOpts.timeoutMs !== null && rawOpts.timeoutMs !== ""
        ? Number(rawOpts.timeoutMs)
        : NaN;
    const safeIntervalMs = Number.isFinite(rawInterval) ? Math.max(10, rawInterval) : 500;
    const safeTimeoutMs = Number.isFinite(rawTimeout) ? Math.max(0, rawTimeout) : 60000;
    if (safeTimeoutMs <= 0) {
      return Promise.resolve(
        getVerdict(serviceUrl, jobId, Object.assign({}, rawOpts, { timeoutMs: 200 }))
      ).then(function (res) {
        if (res && res.job && typeof rawOpts.onProgress === "function") {
          try {
            rawOpts.onProgress(res.job);
          } catch (_e) {}
        }
        if (!res || !res.ok) {
          return res || { ok: false, status: 0, error: "pollVerdict failed" };
        }
        const st = res.job && res.job.status;
        if (st === "done") {
          return { ok: true, status: res.status || 200, job: res.job };
        }
        if (st === "error") {
          return {
            ok: false,
            status: res.status || 200,
            job: res.job,
            error: (res.job && res.job.error) || "verdict job failed",
          };
        }
        return {
          ok: false,
          status: 408,
          job: res.job || null,
          error: "Polling timed out waiting for verdict",
          timedOut: true,
        };
      });
    }
    return _origPollVerdict(
      serviceUrl,
      jobId,
      Object.assign({}, rawOpts, {
        intervalMs: safeIntervalMs,
        timeoutMs: safeTimeoutMs,
      })
    );
  }
  async function _origPollVerdict(serviceUrl, jobId, options = {}) {
  const opts = options && typeof options === 'object' ? options : {};
  const intervalMs = Number(opts.intervalMs) > 0 ? Number(opts.intervalMs) : 1000;
  const timeoutMs = Number(opts.timeoutMs) > 0 ? Number(opts.timeoutMs) : 60000;
  const perRequestTimeoutMs = Number(opts.requestTimeoutMs) > 0 ? Number(opts.requestTimeoutMs) : timeoutMs;
  const deadline = Date.now() + timeoutMs;
  let lastRes = null;

  while (Date.now() < deadline) {
    const remainingMs = deadline - Date.now();
    if (remainingMs <= 0) break;
    const reqTimeout = Math.max(1, Math.min(perRequestTimeoutMs, remainingMs));
    const res = await getVerdict(
      serviceUrl,
      jobId,
      Object.assign({}, opts, { requestTimeoutMs: reqTimeout, timeoutMs: reqTimeout })
    );
    lastRes = res;
    if (!res || !res.ok) {
      return res || { ok: false, offline: true, error: 'Empty verdict response' };
    }
    const job = res.job;
    if (typeof opts.onProgress === 'function' && job) {
      try { opts.onProgress(job); } catch (_) {}
    }
    if (job && job.status === 'done') {
      return { ok: true, id: job.id || jobId, status: 'done', job, result: job.result || null };
    }
    if (job && job.status === 'error') {
      return {
        ok: false,
        offline: false,
        id: job.id || jobId,
        status: 'error',
        job,
        error: job.error || 'Verdict job failed'
      };
    }
    const sleepMs = Math.min(intervalMs, Math.max(0, deadline - Date.now()));
    if (sleepMs <= 0) break;
    await new Promise((r) => setTimeout(r, sleepMs));
  }

  return {
    ok: false,
    offline: true,
    timeout: true,
    job: lastRes && lastRes.job ? lastRes.job : null,
    error: 'Polling timed out after ' + timeoutMs + 'ms'
  };
}

async function _origPollVerdict(baseUrl, jobId, options) {
    var opts = options || {};
    var intervalMs = typeof opts.intervalMs === "number" && opts.intervalMs >= 0 ? opts.intervalMs : 800;
    var timeoutMs = typeof opts.timeoutMs === "number" && opts.timeoutMs > 0 ? opts.timeoutMs : 60000;
    var onProgress = typeof opts.onProgress === "function" ? opts.onProgress : null;
    var startMs = Date.now();

    while (true) {
      var res = await getVerdict(baseUrl, jobId, opts);
      if (!res.ok) {
        return res;
      }
      var job = res.job || {};
      if (onProgress) {
        try {
          onProgress(job);
        } catch (_err) {}
      }
      if (job.status === "done") {
        return res;
      }
      if (job.status === "error") {
        return {
          ok: false,
          offline: false,
          status: 200,
          error: job.error || "Verdict job failed on server.",
          job: job,
          proofUrl: res.proofUrl
        };
      }
      if (Date.now() - startMs >= timeoutMs) {
        return {
          ok: false,
          offline: false,
          timedOut: true,
          status: 408,
          error: "Timed out waiting for verdict (status: " + (job.status || "unknown") + ").",
          job: job,
          proofUrl: res.proofUrl
        };
      }
      await sleep(intervalMs);
    }
  }

  function formatVerdictSummary(jobOrResult, baseUrl) {
    var base = normalizeServiceUrl(baseUrl);
    var job = (jobOrResult && typeof jobOrResult === "object") ? jobOrResult : {};
    var result = (job.result && typeof job.result === "object") ? job.result : job;
    var detection = (result.detection && typeof result.detection === "object") ? result.detection : result;
    var witness = (detection.witness && typeof detection.witness === "object") ? detection.witness : {};
    var stamp = (detection.stamp && typeof detection.stamp === "object") ? detection.stamp : null;
    var verdictObj = (result.verdict && typeof result.verdict === "object") ? result.verdict : {};

    var rawVerdict = witness.verdict || result.verdict_code || verdictObj.integrity || "unknown";
    var jobId = job.id || result.verdict_id || "";
    var proofPath = result.page_url || (jobId ? ("/v/" + encodeURIComponent(jobId)) : "");
    var proofUrl = proofPath
      ? (/^https?:\/\//i.test(proofPath) ? proofPath : base + (proofPath.charAt(0) === "/" ? proofPath : "/" + proofPath))
      : "";

    var removedList = Array.isArray(witness.removed) ? witness.removed : [];
    var cuts = [];
    for (var i = 0; i < removedList.length; i++) {
      var r = removedList[i] || {};
      var srcS = Array.isArray(r.src_s) && r.src_s.length === 2
        ? Math.max(0, Number(r.src_s[1]) - Number(r.src_s[0]))
        : null;
      cuts.push({
        afterClipS: typeof r.after_clip_s === "number" ? r.after_clip_s : null,
        removedS: srcS !== null && !isNaN(srcS) ? Number(srcS.toFixed(3)) : null,
        words: typeof r.words === "string" ? r.words : ""
      });
    }

    if (cuts.length === 0 && stamp && Array.isArray(stamp.cuts)) {
      for (var j = 0; j < stamp.cuts.length; j++) {
        var sc = stamp.cuts[j] || {};
        cuts.push({
          afterClipS: typeof sc.at_clip_s === "number" ? sc.at_clip_s : null,
          removedS: typeof sc.removed_s === "number" ? sc.removed_s : null,
          words: ""
        });
      }
    }

    var stampPresent = Boolean(stamp && stamp.present === true);
    var stampSummary = {
      present: stampPresent,
      venue: stamp && stamp.venue ? String(stamp.venue) : null,
      recordedAtUtc: stamp && stamp.recorded_at_utc ? String(stamp.recorded_at_utc) : null,
      layer1Score: stamp && typeof stamp.layer1_score === "number" ? stamp.layer1_score : null,
      layer2Score: stamp && typeof stamp.layer2_score === "number" ? stamp.layer2_score : null
    };

    var headline = "";
    if (typeof result.headline === "string" && result.headline.trim()) {
      headline = result.headline.trim();
    } else if (typeof verdictObj.headline === "string" && verdictObj.headline.trim()) {
      headline = verdictObj.headline.trim();
    } else if (rawVerdict === "faithful" || rawVerdict === "intact") {
      headline = "Faithful clip: no internal cuts detected.";
    } else if (rawVerdict === "cut" || rawVerdict === "edited") {
      if (cuts.length > 0 && cuts[0].words) {
        headline = "Cut detected: omitted \"" + cuts[0].words + "\".";
      } else {
        headline = "Cut detected in clip.";
      }
    } else if (rawVerdict === "spliced") {
      headline = "Spliced clip: non-contiguous or out-of-order segments detected.";
    } else if (rawVerdict === "not_found") {
      headline = "No matching witness stream found for this clip.";
    } else {
      headline = "Verdict: " + String(rawVerdict);
    }

    return {
      id: jobId,
      verdict: String(rawVerdict),
      headline: headline,
      stream: witness.stream || null,
      cuts: cuts,
      stamp: stampSummary,
      proofUrl: proofUrl
    };
  }

  return {
    DEFAULT_SERVICE_URL: DEFAULT_SERVICE_URL,
    normalizeServiceUrl: normalizeServiceUrl,
    extractClipCandidate: extractClipCandidate,
    submitClip: submitClip,
    getVerdict: getVerdict,
    pollVerdict: pollVerdict,
    formatVerdictSummary: formatVerdictSummary
  };
});
