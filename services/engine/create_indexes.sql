-- create_indexes.sql
-- Run this in PostgreSQL to dramatically improve performance

-- Critical indexes for queries
CREATE INDEX idx_interactions_user_id ON interactions(user_id);
CREATE INDEX idx_interactions_item_id ON interactions(item_id);
CREATE INDEX idx_interactions_created_at ON interactions(created_at DESC);
CREATE INDEX idx_interactions_composite ON interactions(user_id, item_id, rating);

CREATE INDEX idx_items_category ON items(category);
CREATE INDEX idx_items_price ON items(price);
CREATE INDEX idx_items_in_stock ON items(in_stock) WHERE in_stock = true;
CREATE INDEX idx_items_popularity ON items(popularity_score DESC);

-- Full-text search
ALTER TABLE items ADD COLUMN search_vector tsvector;
UPDATE items SET search_vector = to_tsvector('english', coalesce(title,'') || ' ' || coalesce(description,'') || ' ' || coalesce(category,''));
CREATE INDEX idx_items_search ON items USING gin(search_vector);

-- Create trigger to auto-update search vector
CREATE OR REPLACE FUNCTION items_search_trigger() RETURNS trigger AS $$
BEGIN
  NEW.search_vector := to_tsvector('english', coalesce(NEW.title,'') || ' ' || coalesce(NEW.description,'') || ' ' || coalesce(NEW.category,''));
  RETURN NEW;
END
$$ LANGUAGE plpgsql;

CREATE TRIGGER tsvectorupdate BEFORE INSERT OR UPDATE ON items
FOR EACH ROW EXECUTE PROCEDURE items_search_trigger();
