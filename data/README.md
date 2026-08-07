# Local data files

CSV datasets are intentionally excluded from Git. Some source files exceed
GitHub's normal file-size limit, and the planned AWS architecture will store
the application data in PostgreSQL/PostGIS on Amazon RDS.

For local testing of the current CSV-based version, obtain the five original
datasets from the team and place them in this directory using the filenames
referenced in `app.py`.

