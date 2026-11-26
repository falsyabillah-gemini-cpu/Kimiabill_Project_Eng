/**
 * Everyday Sci-Tech International Studio — Google Drive + YouTube Shorts + Email + Telegram Chatbot Bridge
 * (Google Apps Script — 100% Free, Zero Google Cloud Console Setup)
 *
 * Script Properties to configure in Apps Script (Project Settings -> Script Properties):
 * - TELEGRAM_BOT_TOKEN : Bot token from @BotFather (dedicated bot for this channel)
 * - TELEGRAM_CHAT_ID   : Your personal Telegram numeric chat ID
 * - GITHUB_PAT         : GitHub Personal Access Token (repo scope) to trigger repository_dispatch
 * - GITHUB_REPO        : "falsyabillah-gemini-cpu/Kimiabill_Project_Eng" (default)
 * - BOT_NAME           : Optional display name for bot replies (e.g. "Bill_Explains")
 * - ROOT_FOLDER        : Optional Google Drive root folder (default: "EverydaySciTech_Studio")
 */

const DEFAULT_NOTIFY_EMAIL = "researchfalsyabillah@gmail.com";
const DEFAULT_ROOT_FOLDER = "EverydaySciTech_Studio";
const DEFAULT_GITHUB_REPO = "falsyabillah-gemini-cpu/Kimiabill_Project_Eng";
const OPTIONAL_SECRET_TOKEN = "";

function _jsonResponse(obj) {
  return ContentService
    .createTextOutput(JSON.stringify(obj))
    .setMimeType(ContentService.MimeType.JSON);
}

function _getOrCreateFolder(name, parentFolder) {
  const iter = parentFolder
    ? parentFolder.getFoldersByName(name)
    : DriveApp.getFoldersByName(name);
  if (iter.hasNext()) {
    return iter.next();
  }
  return parentFolder ? parentFolder.createFolder(name) : DriveApp.createFolder(name);
}

function _tgHtmlOk() {
  return HtmlService.createHtmlOutput("OK");
}

function _replyTg(botToken, chatId, replyText) {
  try {
    const tgResp = UrlFetchApp.fetch("https://api.telegram.org/bot" + botToken + "/sendMessage", {
      method: "post",
      contentType: "application/json",
      payload: JSON.stringify({ chat_id: String(chatId), text: replyText }),
      muteHttpExceptions: true,
    });
    const code = tgResp.getResponseCode();
    const body = tgResp.getContentText().slice(0, 200);
    console.log("[tg_webhook] sendMessage HTTP " + code + ": " + body);
    return { ok: code >= 200 && code < 300, code: code, body: body };
  } catch (e) {
    console.log("[tg_webhook] sendMessage exception: " + String(e));
    return { ok: false, code: 0, body: String(e) };
  }
}

function _processTelegramUpdate(payload) {
  const props = PropertiesService.getScriptProperties();
  const botToken = String(props.getProperty("TELEGRAM_BOT_TOKEN") || "").trim();
  const allowedChatId = String(props.getProperty("TELEGRAM_CHAT_ID") || "").trim();
  const githubPat = String(props.getProperty("GITHUB_PAT") || "").trim();
  const githubRepo = String(props.getProperty("GITHUB_REPO") || DEFAULT_GITHUB_REPO).trim();
  const botName = String(props.getProperty("BOT_NAME") || "Everyday Sci-Tech Studio").trim();

  const msg = payload.message || payload.edited_message || {};
  const chatId = msg.chat && msg.chat.id !== undefined ? String(msg.chat.id) : "";
  const text = String(msg.text || msg.caption || "").trim();

  if (!botToken || !allowedChatId) {
    console.log("[tg_webhook] ignored: Script Properties incomplete (botTokenSet=" + Boolean(botToken) + ", allowedChatIdSet=" + Boolean(allowedChatId) + ")");
    return { ok: true, ignored: true, reason: "missing_script_properties" };
  }
  if (chatId !== allowedChatId || !text) {
    console.log("[tg_webhook] ignored: chatId mismatch or empty text (incoming=" + chatId + ", expected=" + allowedChatId + ")");
    return { ok: true, ignored: true, reason: "chat_mismatch_or_empty" };
  }

  if (payload.update_id && typeof CacheService !== "undefined") {
    const cache = CacheService.getScriptCache();
    const dedupeKey = "tg_upd_" + String(payload.update_id);
    if (cache.get(dedupeKey)) {
      return { ok: true, duplicate: true };
    }
    cache.put(dedupeKey, "1", 600);
  }

  const low = text.toLowerCase();
  if (low === "/start" || low === "/help") {
    _replyTg(
      botToken,
      allowedChatId,
      "🤖 " + botName + " Bot Active (Pattern B — Direct to All Agents).\n" +
      "• Send any message = auto-compiled to Agents 1, 2, 3, 5, 6, 7, 8 & Engine for NEXT VIDEOS.\n" +
      "• Send `/rerender <notes>` = immediately produce & re-render a replacement video now!\n" +
      "• Send `/rules` = check active overrides | `/clear_rules` = reset extra overrides."
    );
    return { ok: true };
  }

  if (!githubPat) {
    _replyTg(
      botToken,
      allowedChatId,
      "⚠️ Feedback received, but GITHUB_PAT is not set in Apps Script Properties. Without GITHUB_PAT, feedback will be pulled on the next scheduled run."
    );
    return { ok: true, warning: "GITHUB_PAT missing" };
  }

  const isRulesCmd = low.indexOf("/rules") === 0 || low === "/aturan";
  const isClearCmd = low.indexOf("/clear_rules") === 0 || low.indexOf("/clear") === 0;
  const isRerender = low.indexOf("/rerender") === 0;
  const cleanNote = isRerender
    ? text.slice(9).trim()
    : (isRulesCmd || isClearCmd ? text : (low.indexOf("/feedback") === 0 ? text.slice(9).trim() : text));

  const eventType = isRerender ? "telegram_rerender" : "telegram_feedback";
  const dispatchResp = UrlFetchApp.fetch(
    "https://api.github.com/repos/" + githubRepo + "/dispatches",
    {
      method: "post",
      contentType: "application/json",
      headers: {
        Authorization: "Bearer " + githubPat,
        Accept: "application/vnd.github+json",
      },
      payload: JSON.stringify({
        event_type: eventType,
        client_payload: {
          text: cleanNote,
          feedback: cleanNote,
          mode: isRerender ? "rerender" : "next_video",
        },
      }),
      muteHttpExceptions: true,
    }
  );
  const code = dispatchResp.getResponseCode();
  console.log("[tg_webhook] GitHub dispatch HTTP " + code);
  if (code >= 200 && code < 300) {
    if (isRulesCmd) {
      _replyTg(botToken, allowedChatId, "🔎 Checking active rules across all Agents (~10s)...");
    } else if (isClearCmd) {
      _replyTg(botToken, allowedChatId, "🧹 Sending reset rules command to all Agents...");
    } else {
      _replyTg(
        botToken,
        allowedChatId,
        isRerender
          ? "🔄 On it! Starting full replacement video production with notes:\n" + (cleanNote || "(no extra notes)")
          : "⏳ Feedback received! Compiling automatically into Agents 1, 2, 3, 5, 6, 7, 8 + Engine (~10s)..."
      );
    }
  } else {
    _replyTg(botToken, allowedChatId, "⚠️ Failed to trigger GitHub Actions (HTTP " + code + ").");
  }
  return { ok: code >= 200 && code < 300, status: code };
}

function _handleTelegramWebhook(payload) {
  _processTelegramUpdate(payload);
  return _tgHtmlOk();
}

function testTelegramBot() {
  const props = PropertiesService.getScriptProperties();
  const botToken = String(props.getProperty("TELEGRAM_BOT_TOKEN") || "").trim();
  const allowedChatId = String(props.getProperty("TELEGRAM_CHAT_ID") || "").trim();
  const githubPat = String(props.getProperty("GITHUB_PAT") || "").trim();
  console.log("Script Properties -> botTokenLen=" + botToken.length + ", chatId=" + allowedChatId + ", githubPatSet=" + Boolean(githubPat));
  if (!botToken || !allowedChatId) {
    throw new Error("TELEGRAM_BOT_TOKEN or TELEGRAM_CHAT_ID missing in Script Properties.");
  }

  const infoResp = UrlFetchApp.fetch("https://api.telegram.org/bot" + botToken + "/getWebhookInfo", { muteHttpExceptions: true });
  const infoText = infoResp.getContentText();
  console.log("getWebhookInfo -> " + infoText);

  let webhookSummary = "";
  try {
    const parsed = JSON.parse(infoText);
    const res = parsed.result || {};
    webhookSummary =
      "\n• Webhook URL: " + (res.url || "(empty / polling mode)") +
      "\n• Pending updates: " + (res.pending_update_count || 0) +
      "\n• Last error: " + (res.last_error_message || "(none)");
  } catch (e) {
    webhookSummary = "\n• Webhook info: " + infoText.slice(0, 150);
  }

  const sendRes = _replyTg(
    botToken,
    allowedChatId,
    "🧪 Direct test from Google Apps Script succeeded!" +
      webhookSummary +
      "\n\nIf Last error mentions '302', run enableTelegramPollingMode in Apps Script editor."
  );
  console.log("sendMessage result -> " + JSON.stringify(sendRes));
}

function pollTelegram() {
  const props = PropertiesService.getScriptProperties();
  const botToken = String(props.getProperty("TELEGRAM_BOT_TOKEN") || "").trim();
  if (!botToken) return;

  const lock = LockService.getScriptLock();
  if (!lock.tryLock(5000)) return;
  try {
    let offset = Number(props.getProperty("TG_OFFSET") || "0");
    const resp = UrlFetchApp.fetch(
      "https://api.telegram.org/bot" + botToken + "/getUpdates?offset=" + offset + "&timeout=0&allowed_updates=%5B%22message%22%2C%22edited_message%22%5D",
      { method: "get", muteHttpExceptions: true }
    );
    const data = JSON.parse(resp.getContentText() || "{}");
    if (!data.ok || !Array.isArray(data.result) || data.result.length === 0) {
      return;
    }
    data.result.forEach(function (upd) {
      try {
        _processTelegramUpdate(upd);
      } catch (innerErr) {
        console.log("[pollTelegram] error on update " + upd.update_id + ": " + String(innerErr));
      }
      if (upd.update_id && upd.update_id + 1 > offset) {
        offset = upd.update_id + 1;
      }
    });
    props.setProperty("TG_OFFSET", String(offset));
  } finally {
    lock.releaseLock();
  }
}

/**
 * 100% HTTP 302-proof alternative: delete webhook and poll Telegram every 1 minute via Apps Script trigger.
 */
function enableTelegramPollingMode() {
  const props = PropertiesService.getScriptProperties();
  const botToken = String(props.getProperty("TELEGRAM_BOT_TOKEN") || "").trim();
  const allowedChatId = String(props.getProperty("TELEGRAM_CHAT_ID") || "").trim();
  if (!botToken || !allowedChatId) {
    throw new Error("TELEGRAM_BOT_TOKEN or TELEGRAM_CHAT_ID missing in Script Properties.");
  }

  const delResp = UrlFetchApp.fetch(
    "https://api.telegram.org/bot" + botToken + "/deleteWebhook?drop_pending_updates=false",
    { method: "get", muteHttpExceptions: true }
  );
  console.log("deleteWebhook -> " + delResp.getContentText());

  ScriptApp.getProjectTriggers().forEach(function (tr) {
    if (tr.getHandlerFunction() === "pollTelegram") {
      ScriptApp.deleteTrigger(tr);
    }
  });
  ScriptApp.newTrigger("pollTelegram").timeBased().everyMinutes(1).create();
  pollTelegram();
  _replyTg(botToken, allowedChatId, "✅ 1-minute Apps Script Polling Mode active! Bot is now immune to 302 redirects — send /start, /rules, or any feedback anytime.");
}

function doPost(e) {
  try {
    const payload = JSON.parse(e.postData.contents || "{}");

    if (payload.update_id && (payload.message || payload.edited_message)) {
      return _handleTelegramWebhook(payload);
    }

    if (OPTIONAL_SECRET_TOKEN && payload.token !== OPTIONAL_SECRET_TOKEN) {
      return _jsonResponse({ ok: false, error: "Unauthorized token" });
    }

    const action = payload.action || "";
    const props = PropertiesService.getScriptProperties();
    const defaultRoot = String(props.getProperty("ROOT_FOLDER") || DEFAULT_ROOT_FOLDER).trim();

    if (action === "init_episode") {
      const rootName = payload.root_folder || defaultRoot;
      const episodeName = payload.episode_name || ("ep_" + new Date().getTime());
      const rootFolder = _getOrCreateFolder(rootName, null);
      const shareViewer = String(props.getProperty("SHARE_VIEWER_EMAIL") || "").trim();
      if (shareViewer) {
        try {
          rootFolder.addViewer(shareViewer);
        } catch (svErr) {
          console.log("[init_episode] addViewer skip: " + String(svErr));
        }
      }
      const epFolder = _getOrCreateFolder(episodeName, rootFolder);
      return _jsonResponse({
        ok: true,
        folder_id: epFolder.getId(),
        folder_url: epFolder.getUrl(),
      });
    }

    if (action === "upload_file") {
      const folder = DriveApp.getFolderById(payload.folder_id);
      const bytes = Utilities.base64Decode(payload.content_base64);
      const blob = Utilities.newBlob(
        bytes,
        payload.mime_type || "application/octet-stream",
        payload.filename || "file.bin"
      );
      const existing = folder.getFilesByName(blob.getName());
      while (existing.hasNext()) {
        const oldFile = existing.next();
        try {
          oldFile.setTrashed(true);
        } catch (trErr) {
          try {
            folder.removeFile(oldFile);
          } catch (rmErr) {}
        }
      }
      const file = folder.createFile(blob);
      return _jsonResponse({
        ok: true,
        file_id: file.getId(),
        file_url: file.getUrl(),
        filename: file.getName(),
      });
    }

    if (action === "upload_youtube") {
      if (typeof YouTube === "undefined") {
        return _jsonResponse({
          ok: false,
          error: "Service 'YouTube Data API v3' not added in Apps Script (Services + -> YouTube Data API v3 -> Add).",
        });
      }
      const videoFile = DriveApp.getFileById(payload.video_file_id);
      const videoBlob = videoFile.getBlob();
      const privacy = payload.privacy === "public" ? "public" : "unlisted";
      const resource = {
        snippet: {
          title: String(payload.title || "Everyday Sci-Tech Shorts").slice(0, 95),
          description: String(payload.description || ""),
          tags: Array.isArray(payload.tags) ? payload.tags.slice(0, 15) : ["science", "technology", "shorts"],
          categoryId: "28",
          defaultLanguage: "en",
        },
        status: {
          privacyStatus: privacy,
          selfDeclaredMadeForKids: false,
        },
      };
      const uploaded = YouTube.Videos.insert(resource, "snippet,status", videoBlob);
      const vidId = uploaded && uploaded.id ? uploaded.id : "";
      const ytUrl = vidId ? ("https://youtube.com/shorts/" + vidId) : "";

      let thumbSet = false;
      let thumbErr = "";
      if (vidId && payload.thumbnail_file_id) {
        try {
          const thumbBlob = DriveApp.getFileById(payload.thumbnail_file_id).getBlob();
          YouTube.Thumbnails.set(vidId, thumbBlob);
          thumbSet = true;
        } catch (tErr) {
          thumbErr = String(tErr);
        }
      }

      let commentPosted = false;
      let commentErr = "";
      if (vidId && privacy === "public" && payload.first_comment) {
        try {
          Utilities.sleep(2500);
          YouTube.CommentThreads.insert({
            snippet: {
              videoId: vidId,
              topLevelComment: {
                snippet: {
                  textOriginal: String(payload.first_comment).slice(0, 800),
                },
              },
            },
          }, "snippet");
          commentPosted = true;
        } catch (cErr) {
          commentErr = String(cErr);
        }
      }

      let repliedViewer = false;
      let replyErr = "";
      if (vidId && privacy === "public" && payload.reply_to_comment_id && payload.reply_comment_text) {
        try {
          const rText = String(payload.reply_comment_text).replace(/\{YOUTUBE_URL\}/g, ytUrl).slice(0, 800);
          YouTube.Comments.insert({
            snippet: {
              parentId: String(payload.reply_to_comment_id),
              textOriginal: rText,
            },
          }, "snippet");
          repliedViewer = true;
        } catch (rErr) {
          replyErr = String(rErr);
        }
      }

      return _jsonResponse({
        ok: true,
        video_id: vidId,
        youtube_url: ytUrl,
        privacy: privacy,
        thumbnail_set: thumbSet,
        thumbnail_error: thumbErr,
        comment_posted: commentPosted,
        comment_error: commentErr,
        replied_viewer: repliedViewer,
        reply_error: replyErr,
      });
    }

    if (action === "list_flow_clips") {
      const rootName = payload.root_folder || defaultRoot;
      const folderName = payload.folder_name || "Flow_Clip_Bank";
      const rootFolder = _getOrCreateFolder(rootName, null);
      const bankFolder = _getOrCreateFolder(folderName, rootFolder);
      const clips = [];
      const files = bankFolder.getFiles();
      while (files.hasNext()) {
        const f = files.next();
        if (f.isTrashed()) continue;
        const nm = f.getName();
        const low = nm.toLowerCase();
        if (low.endsWith(".mp4") || low.endsWith(".mov") || low.endsWith(".webm")) {
          clips.push({
            file_id: f.getId(),
            filename: nm,
            size_bytes: f.getSize(),
            updated_at: f.getLastUpdated().toISOString(),
            url: f.getUrl(),
          });
        }
      }
      return _jsonResponse({
        ok: true,
        folder_id: bankFolder.getId(),
        folder_url: bankFolder.getUrl(),
        clips: clips,
      });
    }

    if (action === "download_file" || action === "download_drive_file") {
      const fileId = String(payload.file_id || "").trim();
      if (!fileId) {
        return _jsonResponse({ ok: false, error: "file_id is required" });
      }
      const f = DriveApp.getFileById(fileId);
      const bytes = f.getBlob().getBytes();
      if (bytes.length > 35 * 1024 * 1024) {
        return _jsonResponse({ ok: false, error: "File exceeds 35MB Apps Script limit (" + bytes.length + " bytes)" });
      }
      return _jsonResponse({
        ok: true,
        file_id: f.getId(),
        filename: f.getName(),
        mime_type: f.getMimeType(),
        content_base64: Utilities.base64Encode(bytes),
      });
    }

    if (action === "post_comment") {
      if (typeof YouTube === "undefined") {
        return _jsonResponse({ ok: false, error: "YouTube service not enabled" });
      }
      const vidId = String(payload.video_id || "").trim();
      const cText = String(payload.text || payload.comment_text || "").trim();
      if (!vidId || !cText) {
        return _jsonResponse({ ok: false, error: "video_id & text are required" });
      }
      const cRes = YouTube.CommentThreads.insert({
        snippet: {
          videoId: vidId,
          topLevelComment: {
            snippet: { textOriginal: cText.slice(0, 800) },
          },
        },
      }, "snippet");
      return _jsonResponse({ ok: true, comment_id: cRes && cRes.id ? cRes.id : "" });
    }

    if (action === "set_thumbnail") {
      if (typeof YouTube === "undefined") {
        return _jsonResponse({ ok: false, error: "YouTube service not enabled" });
      }
      const vidId = String(payload.video_id || "").trim();
      const b64 = String(payload.image_base64 || "").trim();
      const thumbFid = String(payload.thumbnail_file_id || "").trim();
      if (!vidId || (!b64 && !thumbFid)) {
        return _jsonResponse({ ok: false, error: "video_id & image_base64 (or thumbnail_file_id) are required" });
      }
      const thumbBlob = b64
        ? Utilities.newBlob(Utilities.base64Decode(b64), "image/jpeg", "cover.jpg")
        : DriveApp.getFileById(thumbFid).getBlob();
      YouTube.Thumbnails.set(vidId, thumbBlob);
      return _jsonResponse({ ok: true, thumbnail_set: true, video_id: vidId });
    }

    if (action === "get_youtube_stats") {
      if (typeof YouTube === "undefined") {
        return _jsonResponse({ ok: false, error: "YouTube Data API v3 not enabled in Apps Script" });
      }
      const chResp = YouTube.Channels.list("snippet,statistics,contentDetails", { mine: true });
      const ch = (chResp.items && chResp.items[0]) || null;
      if (!ch) {
        return _jsonResponse({ ok: false, error: "Channel not found on this Google account" });
      }
      const uploadsId = ch.contentDetails.relatedPlaylists.uploads;
      const maxRes = Math.min(50, Number(payload.max_results || 15));
      const plResp = YouTube.PlaylistItems.list("snippet,contentDetails", {
        playlistId: uploadsId,
        maxResults: maxRes,
      });
      const vidIds = (plResp.items || [])
        .map(function (it) { return it.contentDetails && it.contentDetails.videoId; })
        .filter(Boolean);

      const videos = [];
      if (vidIds.length > 0) {
        const vResp = YouTube.Videos.list("snippet,statistics,contentDetails", {
          id: vidIds.join(","),
        });
        (vResp.items || []).forEach(function (v) {
          const st = v.statistics || {};
          videos.push({
            video_id: v.id,
            title: v.snippet.title,
            published_at: v.snippet.publishedAt,
            youtube_url: "https://youtube.com/shorts/" + v.id,
            views: Number(st.viewCount || 0),
            likes: Number(st.likeCount || 0),
            comments: Number(st.commentCount || 0),
          });
        });
      }

      const recentComments = [];
      try {
        const cmResp = YouTube.CommentThreads.list("snippet", {
          allThreadsRelatedToChannelId: ch.id,
          maxResults: 50,
          order: "time",
        });
        (cmResp.items || []).forEach(function (ct) {
          const top = ct.snippet && ct.snippet.topLevelComment;
          const sn = top && top.snippet;
          if (sn && sn.textDisplay) {
            recentComments.push({
              comment_id: top.id,
              video_id: sn.videoId || "",
              author: sn.authorDisplayName || "Viewer",
              text: String(sn.textOriginal || sn.textDisplay).slice(0, 300),
              likes: Number(sn.likeCount || 0),
              published_at: sn.publishedAt || "",
            });
          }
        });
      } catch (cmErr) {}

      let analyticsEnabled = false;
      const countries = [];
      if (typeof YouTubeAnalytics !== "undefined") {
        try {
          const endD = new Date();
          const startD = new Date(endD.getTime() - 28 * 86400 * 1000);
          const fmt = function (d) { return d.toISOString().slice(0, 10); };
          const cRes = YouTubeAnalytics.Reports.query({
            ids: "channel==MINE",
            startDate: fmt(startD),
            endDate: fmt(endD),
            metrics: "views",
            dimensions: "country",
            sort: "-views",
            maxResults: 15,
          });
          analyticsEnabled = true;
          (cRes.rows || []).forEach(function (r) {
            countries.push({ country: r[0], views: Number(r[1] || 0) });
          });
        } catch (anErr) {}
      }

      return _jsonResponse({
        ok: true,
        analytics_enabled: analyticsEnabled,
        channel: {
          id: ch.id,
          title: ch.snippet.title,
          subscribers: Number(ch.statistics.subscriberCount || 0),
          total_views: Number(ch.statistics.viewCount || 0),
          video_count: Number(ch.statistics.videoCount || 0),
        },
        videos: videos,
        recent_comments: recentComments,
        countries: countries,
      });
    }

    if (action === "sync_audit_sheet") {
      const sheetId = String(payload.sheet_id || "").trim();
      if (!sheetId) {
        return _jsonResponse({ ok: false, error: "sheet_id is required" });
      }
      const ss = SpreadsheetApp.openById(sheetId);
      if (Array.isArray(payload.audit_row)) {
        const aSheet = ss.getSheetByName("Weekly_Audit") || ss.insertSheet("Weekly_Audit");
        if (aSheet.getLastRow() === 0) {
          aSheet.appendRow(["Week_Ending", "Subscribers", "Total_Views", "Tier1_Subs_Pct", "Tier1_Views_Pct", "Full_Subs_Pct", "Full_Views_Pct", "Videos_Audited", "Avg_QA", "Summary"]);
        }
        aSheet.appendRow(payload.audit_row);
      }
      return _jsonResponse({ ok: true });
    }

    if (action === "send_custom_email") {
      const to = payload.to || DEFAULT_NOTIFY_EMAIL;
      const subject = payload.subject || "[Everyday SciTech Studio] Report";
      const body = payload.body || "";
      MailApp.sendEmail({ to: to, subject: subject, body: body });
      return _jsonResponse({ ok: true, emailed_to: to });
    }

    if (action === "notify_email") {
      if (payload.skip_email) {
        return _jsonResponse({ ok: true, skipped_email: true });
      }
      const to = payload.to || DEFAULT_NOTIFY_EMAIL;
      const title = payload.title || payload.episode_name || "New Episode";
      const subject = "[Studio Ready] " + title;
      const lines = [
        "New video ready!",
        "",
        "Episode: " + (payload.episode_name || "-"),
        "Title: " + title,
        "YouTube: " + (payload.youtube_url || payload.youtube_note || "-"),
        "Google Drive: " + (payload.folder_url || "-"),
        "",
        payload.captions_preview || "",
      ];
      MailApp.sendEmail({ to: to, subject: subject, body: lines.join("\n") });
      return _jsonResponse({ ok: true, emailed_to: to });
    }

    return _jsonResponse({ ok: false, error: "Unknown action: " + action });
  } catch (err) {
    return _jsonResponse({ ok: false, error: String(err) });
  }
}

function doGet(e) {
  return ContentService
    .createTextOutput("Everyday Sci-Tech Studio Webhook + Telegram Bot is running.")
    .setMimeType(ContentService.MimeType.TEXT);
}
