-- FoggyCaligo/Sentence_Breaker 663dde2b, Apache-2.0; see rust/third_party.

CREATE TABLE IF NOT EXISTS alph_nodes (
    alph TEXT PRIMARY KEY,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS sentence_layers (
    sentence_id TEXT PRIMARY KEY,
    char_length INTEGER NOT NULL,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS alph_links (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    source_alph TEXT NOT NULL,
    target_alph TEXT NOT NULL,
    sentence_id TEXT NOT NULL,
    position INTEGER NOT NULL,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (source_alph) REFERENCES alph_nodes(alph),
    FOREIGN KEY (target_alph) REFERENCES alph_nodes(alph),
    FOREIGN KEY (sentence_id) REFERENCES sentence_layers(sentence_id)
);

CREATE INDEX IF NOT EXISTS idx_alph_links_pair
ON alph_links (source_alph, target_alph);

CREATE INDEX IF NOT EXISTS idx_alph_links_sentence_position
ON alph_links (sentence_id, position);

CREATE INDEX IF NOT EXISTS idx_alph_links_source_sentence
ON alph_links (source_alph, sentence_id);
