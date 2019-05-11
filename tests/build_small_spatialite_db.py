import sqlite3

# This script generates the spatialite.db file in our tests directory.


def generate_it(filename):
    conn = sqlite3.connect(filename)
    # Lead the spatialite extension:
    conn.enable_load_extension(True)
    conn.load_extension("/usr/local/lib/mod_spatialite.dylib")
    conn.execute("select InitSpatialMetadata(1)")
    conn.executescript("create table museums (name text)")
    conn.execute("SELECT AddGeometryColumn('museums', 'point_geom', 4326, 'POINT', 2);")
    # At this point it is around 5MB - we can shrink it dramatically by doing thisO
    conn.execute("delete from spatial_ref_sys")
    conn.execute("delete from spatial_ref_sys_aux")
    conn.commit()
    conn.execute("vacuum")
    conn.close()


if __name__ == "__main__":
    generate_it("spatialite.db")
