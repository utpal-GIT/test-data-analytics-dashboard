# Test Analytics Dashboard

A Streamlit application for analysing patient diagnostic test data: fit a
calibration model between the analyzer's raw response (Abs / Time / signal)
and the actual patient result, back-calculate the predicted patient value,
and report agreement and CLIA-2025 acceptance metrics.

## Features

- **Login + roles** — admin and user. Sessions last 3 hours. Default admin
  account on first run is `admin / admin`; change it from User Management.
  Passwords are stored hashed (PBKDF2-SHA256), never in plain text.
- **Per-user data** — each user's patient table and parameter configurations
  are private. Admins can manage user accounts but do not see other users'
  patient data.
- **Test Analytics tab**
  - Choose a configured test parameter and a calibration model: Linear with
    intercept, Linear without intercept, 4PL Logistic, 5PL Logistic.
  - Fitted model coefficients plus R², RMSE, MAE, N.
  - Editable data grid with 10 empty rows (paste from Excel works), dynamic
    add, row select / clear-selected / clear-all, and column sorting via the
    grid header menu.
  - Sortable computed view below the editor with `Predicted`, `Error%`,
    `|Error%|`, `Bias`, and `In Range`. Rows whose Actual or Predicted falls
    outside the configured detection range are highlighted in red.
  - **Confusion matrix counts** table — TP, TN, FP, FN, sample count,
    average `|Error%|`, and `% within CLIA`. Positive = result outside the
    configured normal range (sex-specific).
  - **Diagnostic performance** table — Sensitivity, Specificity, PPV, NPV,
    Diagnostic Accuracy.
  - Static left-side filter rail: Device ID, Sample ID, Reagent LOT, Date
    range, Age range, Gender, Error% range, |Error%| range, Bias range, and
    In Range.
  - Three plots (Plotly, fully interactive — zoom, hover, click legend to
    hide series). Editable axis titles and chart titles. Use the sidebar
    "Exclude Sample IDs from plots" picker to drop specific points:
    1. Abs vs Actual scatter with the fitted calibration curve overlaid.
       Out-of-detection points marked with a red ✕.
    2. Passing-Bablok regression of Actual vs Predicted with identity line.
       Slope and intercept (with 95% CIs) printed below.
    3. Bland-Altman (mean vs Predicted − Actual) with bias and ±1.96 SD
       limits of agreement.
- **Configurations tab** — add, edit, or delete a test parameter:
  - Normal range (sex-specific: Male / Female)
  - Detection range (low / high)
  - CLIA acceptance window in three modes:
    - `value` — `TV ± absolute`
    - `percent` — `TV ± percent of TV`
    - `threshold` — `TV ± absolute` when `|TV| ≤ T`, else `TV ± percent`
- **User Management tab (admin only)** — list every user with their username
  and how the password is stored, add, edit, or delete. Passwords are hashed,
  so they cannot be displayed; the edit form sets a new one and leaving it
  blank keeps the current password. Cannot delete your own account or the only
  remaining admin.

## How to run

**PowerShell:**

```
cd "D:\Claude Projects\Test Data Analytics Dashboard"
pip install -r requirements.txt
streamlit run app.py
```

**Command Prompt (cmd.exe)** — use `/d` to switch drive in one step:

```
cd /d "D:\Claude Projects\Test Data Analytics Dashboard"
pip install -r requirements.txt
streamlit run app.py
```

Streamlit opens at <http://localhost:8501>.

The SQLite database `app_data.db` is created in this folder on first launch.
Delete it to reset all users, samples, and configurations.

## Storage

By default the app stores everything in the local SQLite file `app_data.db`,
which survives restarts and needs no setup.

Hosts with an ephemeral filesystem — Streamlit Cloud among them — wipe that
file on every restart, which loses samples and parameter configurations. To
keep data there, point the app at an external database. Any SQLAlchemy URL
works; a free Postgres from Neon or Supabase is enough for this data.

1. Create a database and copy its connection string.
2. Put it in `.streamlit/secrets.toml` (gitignored — see
   `.streamlit/secrets.toml.example`):

   ```toml
   [database]
   url = "postgresql://USER:PASSWORD@HOST/DBNAME?sslmode=require"
   ```

   On Streamlit Cloud paste the same into Settings → Secrets. `DATABASE_URL`
   in the environment works too.
3. Copy the existing local data across, once:

   ```
   python migrate_to_postgres.py --dry-run
   python migrate_to_postgres.py
   ```

The header band shows which back end is live — "Local file" or "Postgres".
Tables are created automatically on first connection, and `postgres://` URLs
are accepted (rewritten for SQLAlchemy, with `sslmode=require` added for
remote hosts).

Passwords are stored as PBKDF2-SHA256 hashes. Rows written before hashing
existed are still accepted at login and upgraded to a hash on the spot. Since
a hash cannot be read back, User Management shows how each password is stored
rather than its value, and the edit form sets a new one — leave it blank to
keep the current password.

## File layout

```
app.py                       # entry point + tab routing
auth.py                      # login, sessions, password change UI
db.py                        # schema + per-user CRUD (SQLite or Postgres)
passwords.py                 # PBKDF2 password hashing
migrate_to_postgres.py       # one-off copy of the local data to a server
clia.py                      # CLIA acceptance + range evaluation
models.py                    # linear / 4PL / 5PL fits, Passing-Bablok
metrics.py                   # row metrics + confusion + diagnostic perf
views/test_analytics.py      # main analytics tab
views/configurations.py      # parameter CRUD tab
views/user_management.py     # admin user-management tab
requirements.txt
```

## Notes on the model

`Predicted` is computed by fitting `Actual = f(Abs)` and applying that fit
to each row's `Abs`. So the calibration model takes the analyzer's raw
response and back-calculates the patient's clinical value.

The Passing-Bablok plot is a *separate* method-comparison check between the
ground-truth `Actual` and the model's `Predicted` — it does not influence
the calibration fit itself.

## Stretch ideas (not built)

- CSV / XLSX import on the Test Analytics tab
- Persist the user's chart edits across sessions
- Multi-parameter dashboards on a single page
