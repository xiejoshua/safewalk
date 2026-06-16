-- seed.sql — 8 demo gap_reports pins on the Gillem Logistics corridor
-- Gillem corridor runs roughly along Jonesboro Rd SW / Old Dixie Hwy, Forest Park GA
-- Coordinates verified to fall within the demo bbox (-84.42, 33.68, -84.33, 33.72)

insert into public.gap_reports (geom, type, note, status) values
    (st_point(-84.412, 33.690)::geography, 'broken_sidewalk',  'Large crack across full path width', 'open'),
    (st_point(-84.408, 33.693)::geography, 'no_sidewalk',      'No sidewalk on north side of Jonesboro Rd', 'open'),
    (st_point(-84.405, 33.695)::geography, 'no_crossing',      'Missing ADA ramp at intersection', 'open'),
    (st_point(-84.401, 33.697)::geography, 'obstruction',      'Utility pole blocking path', 'open'),
    (st_point(-84.397, 33.699)::geography, 'broken_sidewalk',  'Heaved slabs from tree roots', 'open'),
    (st_point(-84.393, 33.701)::geography, 'no_sidewalk',      'Sidewalk ends abruptly', 'open'),
    (st_point(-84.389, 33.703)::geography, 'streetlight_out',  'Dark stretch at night — no lighting', 'open'),
    (st_point(-84.385, 33.706)::geography, 'obstruction',      'Overgrown vegetation blocks path', 'open');
