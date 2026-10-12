-- @-mentions as a network tie (propagate/edges.py: build_mentions).
--
-- Handles are matched case-insensitively — TikTok treats them that way and
-- captions do not preserve the account's own casing — so the join is on
-- lower(handle). The plain (platform_id, handle) index from 001 cannot serve
-- that expression, and without this the mentions build is a full scan of
-- accounts per mention.

CREATE INDEX idx_accounts_handle_ci ON accounts(platform_id, lower(handle));
