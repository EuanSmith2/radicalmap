CREATE TABLE IF NOT EXISTS videos (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    url TEXT UNIQUE,
    tiktok_id TEXT,
    username TEXT,
    caption TEXT,
    view_count INTEGER,
    like_count INTEGER,
    comment_count INTEGER,
    share_count INTEGER,
    scraped_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    caption_flagged INTEGER DEFAULT 0,
    caption_flag_reason TEXT DEFAULT '',
    caption_flag_categories TEXT DEFAULT '',
    username_flagged INTEGER DEFAULT 0,
    username_flag_reason TEXT DEFAULT ''
);

CREATE TABLE IF NOT EXISTS accounts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    username TEXT UNIQUE,
    bio TEXT,
    follower_count INTEGER,
    following_count INTEGER,
    video_count INTEGER,
    bio_flagged INTEGER DEFAULT 0,
    bio_flag_reason TEXT DEFAULT '',
    bio_flag_categories TEXT DEFAULT ''
);

CREATE TABLE IF NOT EXISTS hashtags (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    tag TEXT UNIQUE,
    category TEXT,
    display_name TEXT,
    definition TEXT,
    origin TEXT,
    severity TEXT CHECK(severity IN ('low', 'medium', 'high')),
    usage_count INTEGER DEFAULT 0
);

CREATE TABLE IF NOT EXISTS video_hashtags (
    video_id INTEGER REFERENCES videos(id),
    hashtag_id INTEGER REFERENCES hashtags(id),
    PRIMARY KEY (video_id, hashtag_id)
);

CREATE TABLE IF NOT EXISTS external_links (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    source_type TEXT,
    source_id INTEGER,
    platform TEXT,
    url TEXT,
    context TEXT
);

CREATE TABLE IF NOT EXISTS hashtag_cooccurrence (
    hashtag_a_id INTEGER REFERENCES hashtags(id),
    hashtag_b_id INTEGER REFERENCES hashtags(id),
    count INTEGER DEFAULT 1,
    PRIMARY KEY (hashtag_a_id, hashtag_b_id)
);

CREATE TABLE IF NOT EXISTS comments (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    video_id INTEGER REFERENCES videos(id),
    author TEXT,
    text TEXT,
    like_count INTEGER DEFAULT 0,
    flagged INTEGER DEFAULT 0,
    flag_reason TEXT,
    flag_categories TEXT,
    severity TEXT DEFAULT 'low',
    timestamp INTEGER,
    UNIQUE(video_id, author, text)
);

CREATE TABLE IF NOT EXISTS transcripts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    video_id INTEGER REFERENCES videos(id),
    content TEXT,
    language TEXT DEFAULT 'en',
    UNIQUE(video_id)
);

CREATE TABLE IF NOT EXISTS stickers (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    video_id INTEGER REFERENCES videos(id),
    sticker_id TEXT,
    sticker_type TEXT,
    description TEXT,
    flagged INTEGER DEFAULT 0
);
