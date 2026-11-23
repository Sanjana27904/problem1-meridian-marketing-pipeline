# Meridian – Unified Marketing Data Pipeline

## 1. Overview

Meridian is a Python-based ETL pipeline that consolidates advertising, CRM lead, and order data into a unified PostgreSQL database.

It ingests advertising data through a REST API, CRM records through CSV files, and order records through JSON files. The pipeline performs data cleaning, validation, deduplication, and incremental processing before building analytical tables in PostgreSQL. It also tracks rejected records and performs reconciliation to verify data processing outcomes.

The project demonstrates a structured approach to multi-source data integration and reliable data processing.

## 2. Architecture

The solution follows a layered ETL architecture:

```text
              DATA SOURCES
                    |
        +-----------+-----------+
        |           |           |
      Ads API     CRM CSV    Orders JSON
        |           |           |
        +-----------+-----------+
                    |
                    v
              EXTRACTION
                    |
                    v
                RAW DATA
                    |
                    v
          CLEANING & VALIDATION
                    |
                    v
              DEDUPLICATION
                    |
                    v
              STAGING LAYER
           Ads | Leads | Orders
                    |
                    v
              POSTGRESQL
                    |
                    v
              FINAL BUILD
                    |
                    v
          DIMENSION & FACT TABLES
                    |
                    v
          REPORTING & RECONCILIATION
```

### Layer responsibilities

| Layer | Responsibility |
|---|---|
| Extraction | Retrieves paginated advertising API data and reads CRM CSV and order JSON files. |
| Raw processing | Handles incoming source records and preserves source-level information for processing. |
| Cleaning | Normalizes fields and handles inconsistent data formats. |
| Validation | Identifies records that fail data quality checks. |
| Deduplication | Detects duplicate records and handles updated records. |
| Staging | Processes advertising, lead, and order data independently. |
| Database | Manages PostgreSQL connectivity and database operations. |
| Final build | Constructs analytical tables using SQL. |
| Reporting | Produces processing summaries, rejected-record information, and reconciliation results. |

### Final database tables

The pipeline builds the following tables:

- `dim_campaign`
- `fact_ad_spend_daily`
- `dim_customer`
- `fact_leads`
- `fact_orders`
- `fact_order_items`
- `rejected_rows`

## 3. How to Run

### Prerequisites

- Python 3.13 (version used during development)
- PostgreSQL
- PowerShell (Windows) or Bash (Linux/macOS)

### Step 1: Clone the repository

```bash
git clone https://github.com/Sanjana27904/problem1-meridian-marketing-pipeline.git
cd meridian_pipeline
```

Alternatively, download and extract the repository ZIP.

### Step 2: Create and activate a virtual environment

**Windows PowerShell:**

```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
```

**Linux/macOS:**

```bash
python3 -m venv .venv
source .venv/bin/activate
```

### Step 3: Install dependencies

```bash
python -m pip install --upgrade pip
pip install -r requirements.txt
```

### Step 4: Configure PostgreSQL

1. Ensure PostgreSQL is installed and running.
2. Create the database required by the project.
3. Configure the database connection using the settings expected by `meridian/config.py`.
4. Refer to `GUIDE.md` for the database configuration details.

Do not commit database credentials or passwords to the repository.

### Step 5: Execute the pipeline

**Windows PowerShell:**

```powershell
.\run_demo.ps1
```

**Linux/macOS:**

```bash
chmod +x run_demo.sh
./run_demo.sh
```

The demonstration executes the following workflow:

1. Generates synthetic source datasets.
2. Starts the mock advertising API.
3. Performs the initial full load.
4. Re-runs the pipeline with unchanged inputs to test duplicate handling.
5. Simulates the next day's data, including new records and updates.
6. Performs incremental processing.
7. Re-runs unchanged incremental inputs to verify repeatability.

The PowerShell script is the primary execution path tested during development.

## 4. How to Run the Tests

The project uses `pytest` for automated testing.

Activate the virtual environment and execute:

```bash
pytest -q
```

**Latest observed test result:**

```text
31 passed in 11.18s
```

The test suite includes unit-level and pipeline-level tests.

## 5. Design Decisions

| Design decision | Reason |
|---|---|
| Layered ETL architecture | Separates extraction, transformation, validation, staging, and reporting, making the pipeline easier to maintain and debug. |
| Source-specific staging modules | Allows advertising, CRM, and order records to be processed according to their individual data structures. |
| PostgreSQL as the target database | Provides relational storage and supports SQL-based transformations and analytical queries. |
| Incremental processing | Avoids unnecessary full reloads and supports processing new records and updates. |
| Separate rejected-record tracking | Retains visibility into invalid records instead of silently discarding them. |
| SQL-based final data construction | Keeps analytical table creation separate from Python pipeline orchestration. |

### Alternatives considered and rejected

- **Direct source-to-final-table loading:** Rejected in favor of staging and validation to improve separation of responsibilities and data quality control.
- **Repeated full data reloads:** Rejected in favor of incremental processing to avoid unnecessary processing and demonstrate update handling.
- **Silently discarding invalid records:** Rejected in favor of storing rejected records for traceability and investigation.

## 6. Assumptions

1. The advertising REST API is a simulated source representing an external advertising platform.
2. CRM and order datasets are synthetic data generated for this demonstration.
3. Incoming source records may contain duplicates, missing values, inconsistent formats, and invalid data.
4. Subsequent data deliveries may contain new records as well as corrections to existing records.
5. PostgreSQL is available and configured before pipeline execution.
6. The demonstration uses fictional data and does not contain real customer or business information.

## 7. Known Limits and Unfinished Items

- The advertising API is simulated; integration with live advertising platforms is not implemented.
- CRM and order inputs are generated datasets rather than live business feeds.
- Testing has been performed against the included demonstration datasets; production-scale performance has not been established.
- Production scheduling, centralized monitoring, and deployment infrastructure are outside the current implementation scope.
- Production authentication and access management for external data sources would require additional implementation.

## 8. What I Would Do Next

1. Integrate live advertising and CRM platform APIs.
2. Introduce automated scheduling and pipeline orchestration.
3. Add centralized logging, monitoring, and failure alerts.
4. Expand data quality checks and validation coverage.
5. Perform performance testing with larger datasets.
6. Automate deployment and environment configuration.

---

**Technology Stack:** Python, PostgreSQL, SQL, REST API, CSV, JSON, pytest

**Project Focus:** ETL, Multi-source Data Integration, Data Quality, Deduplication, Incremental Processing, and Reconciliation
