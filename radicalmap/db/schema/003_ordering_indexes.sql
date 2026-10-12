-- Ordering indexes found by the 100k-row benchmark.
--
-- 001 indexed items(platform_id, posted_at DESC), which is the right shape for
-- "recent items on TikTok" but useless for "recent items", full stop — a
-- composite index cannot serve an ORDER BY on its second column without an
-- equality constraint on the first. The Explorer's default view has no platform
-- filter, so it was falling back to a full scan plus a sort.
--
-- Cheap to fix, and much cheaper to fix now than once the table is large.

CREATE INDEX idx_items_posted_all    ON items(posted_at DESC);
CREATE INDEX idx_items_likes         ON items(like_count DESC);

-- Same shape of problem on the comment timeline.
CREATE INDEX idx_comments_item_likes ON comments(item_id, like_count DESC);

-- Classification lookups from the Explorer: "show me everything this run
-- labelled antisemitic at high severity", which is the query the whole
-- two-stage design exists to make answerable.
CREATE INDEX idx_class_run_target    ON classifications(run_id, target_type, target_id);
