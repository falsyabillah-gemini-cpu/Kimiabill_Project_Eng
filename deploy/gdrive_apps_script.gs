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

function _answerCallbackQuery(botToken, callbackQueryId, text) {
  try {
    UrlFetchApp.fetch("https://api.telegram.org/bot" + botToken + "/answerCallbackQuery", {
      method: "post",
      contentType: "application/json",
      payload: JSON.stringify({ callback_query_id: String(callbackQueryId), text: String(text || "Processing...").slice(0, 180) }),
      muteHttpExceptions: true,
    });
  } catch (e) {}
}

function _editInlineMarkup(botToken, chatId, messageId, keyboardRows) {
  try {
    UrlFetchApp.fetch("https://api.telegram.org/bot" + botToken + "/editMessageReplyMarkup", {
      method: "post",
      contentType: "application/json",
      payload: JSON.stringify({
        chat_id: String(chatId),
        message_id: Number(messageId),
        reply_markup: { inline_keyboard: keyboardRows || [] },
      }),
      muteHttpExceptions: true,
    });
  } catch (e) {}
}

function _fireGithubDispatch(githubRepo, githubPat, eventType, clientPayload) {
  const resp = UrlFetchApp.fetch(
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
        client_payload: clientPayload || {},
      }),
      muteHttpExceptions: true,
    }
  );
  return resp.getResponseCode();
}

function _processTelegramUpdate(payload) {
  const props = PropertiesService.getScriptProperties();
  const botToken = String(props.getProperty("TELEGRAM_BOT_TOKEN") || "").trim();
  const allowedChatId = String(props.getProperty("TELEGRAM_CHAT_ID") || "").trim();
  const githubPat = String(props.getProperty("GITHUB_PAT") || "").trim();
  const githubRepo = String(props.getProperty("GITHUB_REPO") || DEFAULT_GITHUB_REPO).trim();
  const botName = String(props.getProperty("BOT_NAME") || "Bill_Explains").trim();

  if (!botToken || !allowedChatId) {
    return { ok: true, ignored: true, reason: "missing_script_properties" };
  }

  if (payload.update_id && typeof CacheService !== "undefined") {
    const cache = CacheService.getScriptCache();
    const dedupeKey = "tg_upd_" + String(payload.update_id);
    if (cache.get(dedupeKey)) {
      return { ok: true, duplicate: true };
    }
    cache.put(dedupeKey, "1", 600);
  }

  // Handle Inline Keyboard Button Callbacks (Approve / Revise / Reject / AI Audit)
  if (payload.callback_query) {
    const cq = payload.callback_query;
    const cqId = String(cq.id || "");
    const cqMsg = cq.message || {};
    const cqChatId = cqMsg.chat && cqMsg.chat.id !== undefined ? String(cqMsg.chat.id) : "";
    const cqMsgId = cqMsg.message_id;
    const data = String(cq.data || "").trim();

    if (cqChatId !== allowedChatId) {
      _answerCallbackQuery(botToken, cqId, "Unauthorized chat");
      return { ok: true, ignored: true };
    }
    if (!githubPat) {
      _answerCallbackQuery(botToken, cqId, "GITHUB_PAT not configured");
      return { ok: true, error: "GITHUB_PAT missing" };
    }

    const parts = data.split(":");
    const cmd = parts[0] || "";
    const epId = parts.slice(1).join(":");

    if (cmd === "revise" && epId) {
      _answerCallbackQuery(botToken, cqId, "Select what to fix or reply with text!");
      props.setProperty("PENDING_REVISE_EPISODE_ID", epId);
      _editInlineMarkup(botToken, cqChatId, cqMsgId, [
        [
          { text: "🎥 Fix B-Roll", callback_data: "revise_broll:" + epId },
          { text: "📊 Fix Diagrams", callback_data: "revise_diagrams:" + epId },
        ],
        [
          { text: "🎙️ Fix Script/Audio", callback_data: "revise_script:" + epId },
          { text: "◀️ Back", callback_data: "restore_btns:" + epId },
        ],
      ]);
      _replyTg(botToken, allowedChatId, "✏️ Tap a focus button above for `" + epId + "`, OR send your custom revision instructions as a short text message right now.");
      return { ok: true };
    }

    if (cmd === "reject" && epId) {
      _answerCallbackQuery(botToken, cqId, "Replace with Video Bank or New Topic?");
      _editInlineMarkup(botToken, cqChatId, cqMsgId, [
        [
          { text: "💾 Use Video Bank", callback_data: "reject_backup:" + epId },
          { text: "🆕 Produce New Topic", callback_data: "reject_new:" + epId },
        ],
        [
          { text: "◀️ Cancel Reject", callback_data: "restore_btns:" + epId },
        ],
      ]);
      return { ok: true };
    }

    if (cmd === "restore_btns" && epId) {
      props.deleteProperty("PENDING_REVISE_EPISODE_ID");
      _answerCallbackQuery(botToken, cqId, "Restored options");
      _editInlineMarkup(botToken, cqChatId, cqMsgId, [
        [
          { text: "✅ Approve & Schedule", callback_data: "approve:" + epId },
          { text: "🔄 Revise Video", callback_data: "revise:" + epId },
        ],
        [
          { text: "💾 Save to Video Bank", callback_data: "save_backup:" + epId },
          { text: "❌ Reject Topic", callback_data: "reject:" + epId },
        ],
        [
          { text: "🤖 Delegate to AI", callback_data: "ai_audit:" + epId },
        ],
      ]);
      return { ok: true };
    }

    props.deleteProperty("PENDING_REVISE_EPISODE_ID");
    let eventType = "";
    let dispatchPayload = { episode_id: epId };
    let statusBtnText = "⏳ Dispatched...";
    let replyMsg = "";

    if (cmd === "approve" && epId) {
      eventType = "telegram_approve";
      statusBtnText = "✅ Approved — Scheduling...";
      replyMsg = "✅ Approved `" + epId + "`! Transitioning privacy/publishAt on YouTube...";
    } else if (cmd === "save_backup" && epId) {
      eventType = "telegram_save_backup";
      statusBtnText = "💾 Moving to Video Bank...";
      replyMsg = "💾 Saving `" + epId + "` into Standby Video Bank (kept Unlisted & safe from today's AI auto-publish)...";
    } else if ((cmd === "revise_broll" || cmd === "revise_diagrams" || cmd === "revise_script") && epId) {
      eventType = "telegram_rerender";
      const fbMap = {
        revise_broll: "Owner requested B-roll replacement for mismatched/weak visual shots.",
        revise_diagrams: "Owner requested mechanistic diagram fixes (accuracy, variety, clarity).",
        revise_script: "Owner requested tighter script/audio pacing & pronunciation cleanup.",
      };
      dispatchPayload = { episode_id: epId, feedback: fbMap[cmd], text: fbMap[cmd], mode: "rerender" };
      statusBtnText = "🔄 Revising Video...";
      replyMsg = "🔄 Revising `" + epId + "`: " + fbMap[cmd];
    } else if ((cmd === "reject_new" || cmd === "reject_legacy") && epId) {
      eventType = "telegram_reject_next";
      statusBtnText = "❌ Rejected — Generating Next Topic...";
      replyMsg = "❌ Rejected `" + epId + "`. Blacklisting topic, deleting draft, and generating next topic...";
    } else if (cmd === "reject_backup" && epId) {
      eventType = "telegram_reject_use_backup";
      statusBtnText = "💾❌ Using Video Bank...";
      replyMsg = "💾❌ Rejected `" + epId + "`! Promoting 1 standby video from the Video Bank to replace it...";
    } else if (cmd === "ai_audit" && epId) {
      eventType = "telegram_ai_audit";
      statusBtnText = "🤖 AI Chief Editor Auditing...";
      replyMsg = "🤖 Delegated `" + epId + "` to AI Chief Editor multimodal MP4 audit...";
    } else {
      _answerCallbackQuery(botToken, cqId, "Done");
      return { ok: true };
    }

    _answerCallbackQuery(botToken, cqId, statusBtnText);
    _editInlineMarkup(botToken, cqChatId, cqMsgId, [[{ text: statusBtnText, callback_data: "noop" }]]);
    const code = _fireGithubDispatch(githubRepo, githubPat, eventType, dispatchPayload);
    if (code >= 200 && code < 300) {
      _replyTg(botToken, allowedChatId, replyMsg);
    } else {
      _replyTg(botToken, allowedChatId, "⚠️ Dispatch `" + eventType + "` failed (HTTP " + code + ").");
    }
    return { ok: code >= 200 && code < 300, status: code };
  }

  const msg = payload.message || payload.edited_message || {};
  const chatId = msg.chat && msg.chat.id !== undefined ? String(msg.chat.id) : "";
  const text = String(msg.text || msg.caption || "").trim();

  if (chatId !== allowedChatId || !text) {
    return { ok: true, ignored: true, reason: "chat_mismatch_or_empty" };
  }

  const low = text.toLowerCase();
  if (low === "/start" || low === "/help") {
    _replyTg(
      botToken,
      allowedChatId,
      "🤖 " + botName + " Studio v2 Bot Active.\n" +
      "• Drafts upload as Unlisted first with 5 inline audit buttons (Approve, Revise, Save to Video Bank, Reject, AI Audit).\n" +
      "• Send `/rerender <episode_id optional> <notes>` = immediately re-render replacement.\n" +
      "• Send `/rules` = active overrides | `/clear_rules` = reset extra rules."
    );
    return { ok: true };
  }

  if (!githubPat) {
    _replyTg(botToken, allowedChatId, "⚠️ Message received, but GITHUB_PAT missing in Apps Script Properties.");
    return { ok: true, warning: "GITHUB_PAT missing" };
  }

  const pendingReviseEp = String(props.getProperty("PENDING_REVISE_EPISODE_ID") || "").trim();
  const isRulesCmd = low.indexOf("/rules") === 0 || low === "/aturan";
  const isClearCmd = low.indexOf("/clear_rules") === 0 || low.indexOf("/clear") === 0;
  const isRerenderCmd = low.indexOf("/rerender") === 0;
  const isRerender = isRerenderCmd || (Boolean(pendingReviseEp) && !isRulesCmd && !isClearCmd && low.indexOf("/") !== 0);
  let cleanNote = isRerenderCmd
    ? text.slice(9).trim()
    : (isRulesCmd || isClearCmd ? text : (low.indexOf("/feedback") === 0 ? text.slice(9).trim() : text));

  let epOverride = pendingReviseEp;
  if (isRerenderCmd && cleanNote) {
    const mEp = cleanNote.match(/^(ep_[A-Za-z0-9_]+)\s*(.*)$/);
    if (mEp) {
      epOverride = mEp[1];
      cleanNote = (mEp[2] || "").trim();
    }
  }
  if (pendingReviseEp && isRerender) {
    props.deleteProperty("PENDING_REVISE_EPISODE_ID");
  }

  const eventType = isRerender ? "telegram_rerender" : "telegram_feedback";
  const code = _fireGithubDispatch(githubRepo, githubPat, eventType, {
    episode_id: epOverride,
    text: cleanNote,
    feedback: cleanNote,
    mode: isRerender ? "rerender" : "next_video",
  });
  if (code >= 200 && code < 300) {
    if (isRulesCmd) {
      _replyTg(botToken, allowedChatId, "🔎 Checking active rules (~10s)...");
    } else if (isClearCmd) {
      _replyTg(botToken, allowedChatId, "🧹 Sending reset rules command...");
    } else {
      _replyTg(
        botToken,
        allowedChatId,
        isRerender
          ? "🔄 Starting re-render (" + (epOverride || "latest draft") + ") with notes:\n" + (cleanNote || "(default refinement)")
          : "⏳ Feedback received! Compiling into Agents 1-8 (~10s)..."
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
  if (!botToken || !allowedChatId) {
    throw new Error("TELEGRAM_BOT_TOKEN or TELEGRAM_CHAT_ID missing in Script Properties.");
  }
  const infoResp = UrlFetchApp.fetch("https://api.telegram.org/bot" + botToken + "/getWebhookInfo", { muteHttpExceptions: true });
  _replyTg(
    botToken,
    allowedChatId,
    "🧪 Apps Script v2 Telegram Bridge test OK! (githubPatSet=" + Boolean(githubPat) + ")\n" + infoResp.getContentText().slice(0, 220)
  );
}

function pollTelegram() {
  const props = PropertiesService.getScriptProperties();
  const botToken = String(props.getProperty("TELEGRAM_BOT_TOKEN") || "").trim();
  if (!botToken) return;

  const lock = LockService.getScriptLock();
  if (!lock.tryLock(5000)) return;
  try {
    let offset = Number(props.getProperty("TG_OFFSET") || "0");
    const allowed = encodeURIComponent(JSON.stringify(["message", "edited_message", "callback_query"]));
    const resp = UrlFetchApp.fetch(
      "https://api.telegram.org/bot" + botToken + "/getUpdates?offset=" + offset + "&timeout=0&allowed_updates=" + allowed,
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

function enableTelegramPollingMode() {
  const props = PropertiesService.getScriptProperties();
  const botToken = String(props.getProperty("TELEGRAM_BOT_TOKEN") || "").trim();
  const allowedChatId = String(props.getProperty("TELEGRAM_CHAT_ID") || "").trim();
  if (!botToken || !allowedChatId) {
    throw new Error("TELEGRAM_BOT_TOKEN or TELEGRAM_CHAT_ID missing in Script Properties.");
  }
  UrlFetchApp.fetch("https://api.telegram.org/bot" + botToken + "/deleteWebhook?drop_pending_updates=false", { method: "get", muteHttpExceptions: true });
  ScriptApp.getProjectTriggers().forEach(function (tr) {
    if (tr.getHandlerFunction() === "pollTelegram") ScriptApp.deleteTrigger(tr);
  });
  ScriptApp.newTrigger("pollTelegram").timeBased().everyMinutes(1).create();
  pollTelegram();
  _replyTg(botToken, allowedChatId, "✅ 1-minute Apps Script Polling Mode (with inline buttons) active!");
}

function doPost(e) {
  try {
    const payload = JSON.parse(e.postData.contents || "{}");

    if (payload.update_id && (payload.message || payload.edited_message || payload.callback_query)) {
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

    if (action === "set_youtube_privacy") {
      if (typeof YouTube === "undefined") {
        return _jsonResponse({ ok: false, error: "YouTube service not enabled" });
      }
      const vidId = String(payload.video_id || "").trim();
      const priv = String(payload.privacy || "public").trim().toLowerCase();
      const publishAt = String(payload.publish_at || "").trim();
      if (!vidId) {
        return _jsonResponse({ ok: false, error: "video_id is required" });
      }
      const stObj = {
        privacyStatus: priv === "private" ? "private" : (priv === "unlisted" ? "unlisted" : "public"),
        selfDeclaredMadeForKids: false,
      };
      if (stObj.privacyStatus === "private" && publishAt) {
        stObj.publishAt = publishAt;
      }
      YouTube.Videos.update({ id: vidId, status: stObj }, "status");
      if (payload.first_comment && (stObj.privacyStatus === "public" || publishAt)) {
        try {
          YouTube.CommentThreads.insert({
            snippet: {
              videoId: vidId,
              topLevelComment: { snippet: { textOriginal: String(payload.first_comment).slice(0, 800) } },
            },
          }, "snippet");
        } catch (cErr) {}
      }
      return _jsonResponse({ ok: true, video_id: vidId, privacy: stObj.privacyStatus, publish_at: publishAt || null });
    }

    if (action === "delete_youtube_video") {
      if (typeof YouTube === "undefined") {
        return _jsonResponse({ ok: false, error: "YouTube service not enabled" });
      }
      const vidId = String(payload.video_id || "").trim();
      if (!vidId) {
        return _jsonResponse({ ok: false, error: "video_id is required" });
      }
      YouTube.Videos.remove(vidId);
      return _jsonResponse({ ok: true, deleted: vidId });
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
