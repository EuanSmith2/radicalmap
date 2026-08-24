/**
 * TIKTOK COMMENT EXTRACTOR — paste into Firefox browser console
 *
 * 1. Open Firefox → navigate to a TikTok video
 * 2. Open Dev Tools → Console (F12 or Cmd+Option+I)
 * 3. Paste this entire script and hit Enter
 * 4. A JSON file downloads automatically per video
 * 5. Move the downloaded file to ~/Projects/radicalmap/data/comments/
 * 6. Run: python -m scraper.import_comments
 *
 * To do all videos at once: open each video in a tab and run the script in each.
 */

(async function extractComments() {
  const url = location.href;
  const videoId = url.match(/\/(?:video|photo)\/(\d+)/)?.[1];
  const username = url.match(/tiktok\.com\/@([^/?]+)/)?.[1];

  if (!videoId) {
    alert('Navigate to a TikTok video first, then paste this script.');
    return;
  }

  console.log(`Extracting comments for @${username} / ${videoId}...`);

  let allComments = [];
  let cursor = 0;
  let page = 0;
  const MAX_PAGES = 20; // ~1000 comments max

  while (page < MAX_PAGES) {
    const params = new URLSearchParams({
      aweme_id: videoId,
      count: 50,
      cursor: cursor,
      aid: 1988,
      app_language: 'en',
      app_name: 'tiktok_web',
      browser_language: 'en-US',
      browser_name: 'Mozilla',
      browser_online: 'true',
      browser_platform: 'MacIntel',
      channel: 'tiktok_web',
      device_platform: 'web_pc',
      focus_state: 'true',
      from_page: 'video',
      is_fullscreen: 'false',
      is_page_visible: 'true',
      language: 'en',
      os: 'mac',
      priority_region: 'IE',
      region: 'IE',
      tz_name: Intl.DateTimeFormat().resolvedOptions().timeZone,
      webcast_language: 'en',
    });

    try {
      const resp = await fetch(`https://www.tiktok.com/api/comment/list/?${params}`, {
        credentials: 'include',
        headers: {
          'Accept': 'application/json, text/plain, */*',
          'Referer': location.href,
        },
      });

      const data = await resp.json();

      if (data.status_code !== 0 || !data.comments?.length) {
        console.log(`Stopped at page ${page}: status_code=${data.status_code}`);
        break;
      }

      const mapped = data.comments.map(c => ({
        video_id: videoId,
        username: username,
        author: c.user?.unique_id || c.user?.nickname || '',
        text: c.text || '',
        like_count: c.digg_count || 0,
        timestamp: c.create_time || null,
      }));

      allComments.push(...mapped);
      console.log(`Page ${page + 1}: +${mapped.length} comments (total: ${allComments.length})`);

      if (!data.has_more) break;
      cursor = data.cursor;
      page++;
      await new Promise(r => setTimeout(r, 600));

    } catch (err) {
      console.error('Error on page', page, err);
      break;
    }
  }

  if (!allComments.length) {
    console.warn('No comments extracted. Are you logged in?');
    return;
  }

  // Download JSON file
  const blob = new Blob([JSON.stringify(allComments, null, 2)], { type: 'application/json' });
  const a = document.createElement('a');
  a.href = URL.createObjectURL(blob);
  a.download = `comments_${videoId}.json`;
  document.body.appendChild(a);
  a.click();
  document.body.removeChild(a);

  console.log(`✓ Downloaded ${allComments.length} comments → comments_${videoId}.json`);
  console.log('Move file to ~/Projects/radicalmap/data/comments/ then run: python -m scraper.import_comments');
})();
