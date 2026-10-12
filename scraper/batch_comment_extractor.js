/**
 * RADICALMAP — BATCH COMMENT EXTRACTOR
 * ─────────────────────────────────────
 * Paste into Firefox console while on any tiktok.com page.
 * Downloads one JSON file per video automatically.
 * Move all downloaded files to ~/Projects/radicalmap/data/comments/
 * Then run: python -m scraper.import_comments
 *
 * Runs entirely in YOUR Firefox session — TikTok's JS signs every
 * request with the correct X-Bogus parameter automatically.
 */

(async function batchExtract() {
  // [videoId, username] pairs. Fill in locally from your own collection list;
  // real account handles are never committed to this repository.
  const VIDEOS = [
    // ["7000000000000000000", "example_user"],
  ];

  const MAX_PAGES = 20;   // ~1000 comments max per video
  const DELAY_MS  = 700;  // between API pages
  const VIDEO_DELAY_MS = 1500; // between videos

  function sleep(ms) { return new Promise(r => setTimeout(r, ms)); }

  function download(data, filename) {
    const blob = new Blob([JSON.stringify(data, null, 2)], { type: "application/json" });
    const a = document.createElement("a");
    a.href = URL.createObjectURL(blob);
    a.download = filename;
    document.body.appendChild(a);
    a.click();
    document.body.removeChild(a);
  }

  async function fetchCommentsForVideo(videoId, username) {
    const allComments = [];
    let cursor = 0;

    for (let page = 0; page < MAX_PAGES; page++) {
      const params = new URLSearchParams({
        aweme_id:        videoId,
        count:           50,
        cursor:          cursor,
        aid:             1988,
        app_language:    "en",
        app_name:        "tiktok_web",
        browser_language:"en-US",
        browser_name:    "Mozilla",
        browser_online:  "true",
        browser_platform:"MacIntel",
        channel:         "tiktok_web",
        device_platform: "web_pc",
        focus_state:     "true",
        from_page:       "video",
        is_fullscreen:   "false",
        is_page_visible: "true",
        language:        "en",
        os:              "mac",
        priority_region: "IE",
        region:          "IE",
        tz_name:         Intl.DateTimeFormat().resolvedOptions().timeZone,
        webcast_language:"en",
      });

      let data;
      try {
        const resp = await fetch(`https://www.tiktok.com/api/comment/list/?${params}`, {
          credentials: "include",
          headers: {
            Accept:  "application/json, text/plain, */*",
            Referer: `https://www.tiktok.com/@${username}/video/${videoId}`,
          },
        });
        const text = await resp.text();
        if (!text || text.length === 0) {
          console.warn(`  [!] Empty response for ${videoId} page ${page}`);
          break;
        }
        data = JSON.parse(text);
      } catch (err) {
        console.error(`  [!] Fetch error page ${page}:`, err);
        break;
      }

      if (data.status_code !== 0) {
        console.log(`  [!] status_code=${data.status_code} for ${videoId}`);
        break;
      }

      const comments = data.comments || [];
      if (!comments.length) break;

      for (const c of comments) {
        allComments.push({
          video_id:   videoId,
          username:   username,
          author:     c.user?.unique_id || c.user?.nickname || "",
          text:       c.text || "",
          like_count: c.digg_count || 0,
          timestamp:  c.create_time || null,
        });
      }

      if (!data.has_more) break;
      cursor = data.cursor;
      await sleep(DELAY_MS);
    }

    return allComments;
  }

  // ── Main loop ─────────────────────────────────────────────────
  let totalSaved = 0;
  const results = {};

  for (const [videoId, username] of VIDEOS) {
    console.log(`[${VIDEOS.indexOf([videoId, username]) + 1}/${VIDEOS.length}] @${username} (${videoId})...`);
    const comments = await fetchCommentsForVideo(videoId, username);
    results[videoId] = comments;

    if (comments.length > 0) {
      download(comments, `comments_${videoId}.json`);
      totalSaved += comments.length;
      console.log(`  ✓ ${comments.length} comments downloaded`);
    } else {
      console.log(`  — 0 comments (account may have comments disabled or be restricted)`);
    }

    await sleep(VIDEO_DELAY_MS);
  }

  console.log(`\n✓ DONE. ${totalSaved} total comments across ${VIDEOS.length} videos.`);
  console.log("Move JSON files to ~/Projects/radicalmap/data/comments/");
  console.log("Then run: python -m scraper.import_comments");

  return results;
})();
