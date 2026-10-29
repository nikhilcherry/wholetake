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

  async function submitClip(baseUrl, clipUrl, options) {
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

  async function getVerdict(baseUrl, jobId, options) {
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

  async function pollVerdict(baseUrl, jobId, options) {
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
