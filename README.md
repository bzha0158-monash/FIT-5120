# SilentWaze — Melbourne CBD Navigation

SilentWaze is a Flask web application that suggests walking-route alternatives
and evaluates them using pedestrian crowd predictions stored in Amazon RDS for
PostgreSQL/PostGIS.

## Repository structure

- `Silento_Silent_Ways_Final_version/` — the only active Flask web application
  and the source used to build the AWS Elastic Beanstalk deployment package.
- `lamdba/silento-etl/` — AWS Lambda ETL source code that extracts, validates,
  transforms, and uploads cleaned datasets to Amazon S3.
- `lamdba/silento-etl.zip` — deployment-ready Lambda archive.

## Web application

The Flask application:

- serves the HTML, CSS, and JavaScript frontend;
- queries sensor locations and crowd predictions from Amazon RDS;
- queries sensory-refuge locations from Amazon RDS;
- exposes the data through REST API endpoints;
- uses Nominatim for location search and reverse geocoding; and
- uses OSRM for walking-route alternatives.

The browser evaluates the OSRM alternatives using nearby crowd-prediction data.
The current route calculation does not use the `ROAD_NETWORK` or
`ROUTE_CROWD_SCORE` database tables.

## Run locally

Open PowerShell in `Silento_Silent_Ways_Final_version` and run:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
pip install -r requirements.txt
python Silento_app.py
```

Then open `http://127.0.0.1:5000`.

The following environment variables are required:

- `DB_HOST`
- `DB_PORT`
- `DB_NAME`
- `DB_USER`
- `DB_PASSWORD`

Database credentials must not be committed to Git.

## AWS deployment

The web application is deployed to AWS Elastic Beanstalk. The Beanstalk ZIP
must contain the contents of `Silento_Silent_Ways_Final_version` at the archive
root, including:

- `application.py`
- `Silento_app.py`
- `requirements.txt`
- `static/`
- `templates/`

The ETL Lambda currently uploads cleaned files to Amazon S3 under
`cleaned/runs/...`. Loading those files into RDS is a separate database-loading
step.
