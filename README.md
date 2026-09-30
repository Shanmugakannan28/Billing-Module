# Hospital Management API

## Setup
```
pip install -r requirements.txt
cp .env.example .env
```

**Important — schema changed again:** Level 27 added a `billings` table
with new FK/CHECK constraints. Delete `hospital.db` again before restarting
if you're upgrading from a pre-billing copy of this project:
```
rm -f hospital.db
uvicorn main:app --reload
```

## Getting a working session
1. `POST /api/v1/auth/register` with `{"username": "admin", "password": "...", "role": "admin"}`.
2. `POST /api/v1/auth/login` (or the Swagger **Authorize** button) with the same credentials.
3. Create a doctor as admin, then register a second user with
   `{"role": "doctor", "doctor_id": <id>}` to test doctor-only views.

## Tests
```
pytest
```
Runs with coverage (`pytest-cov`) via `pytest.ini`; fails if total coverage
drops below 70%. `tests/test_api.py` covers Levels 1-10,
`tests/test_advanced.py` covers Levels 11-18, `tests/test_billing.py`
covers Levels 27-29.

## What's implemented (Levels 27-29): Billing
- **27 - Billing module:** new `Billing` model (`patient_id`, `doctor_id`,
  optional `appointment_id`, `consultation_fee`, `additional_charges`,
  `total_amount`, `payment_status`, `payment_mode`, `is_active`, plus the
  usual audit fields). `total_amount` is never accepted from the client —
  `services.create_billing`/`update_billing` always recompute it as
  `consultation_fee + additional_charges`. Before creating a billing:
  the patient and doctor must exist, the doctor must be active, and if an
  `appointment_id` is given it must belong to that same doctor+patient pair,
  must not already be cancelled, and must not already have a billing (both
  an app-level check for a clean error message, and a DB-level `UNIQUE`
  constraint on `appointment_id` as the real guarantee). Endpoints:
  `POST /billings`, `GET /billings`, `GET /billings/{id}`,
  `PUT`/`PATCH /billings/{id}`, `DELETE /billings/{id}` (soft delete via
  `is_active=False`), `GET /patients/{id}/billings`,
  `GET /doctors/{id}/billings`. Only admin can create/update/delete; a
  doctor-role login can only view billing records for their own patients
  (403 otherwise), the same pattern used for patients/appointments in
  Levels 11-12.
- **28 - Reports & filtering:** `GET /billings` takes `payment_status`,
  `doctor_id`, `patient_id`, `date_from`/`date_to` filters plus `page`/
  `limit` pagination, same as every other list endpoint. `GET
  /reports/revenue?doctor_id=&from=&to=` (admin only) returns total
  revenue, a per-doctor breakdown, and a per-day breakdown — all computed
  in SQL (`SUM`/`GROUP BY`), counting only `paid` and still-active
  billings.
- **29 - Transactions & consistency:** creating a billing linked to a
  still-`scheduled` appointment also flips that appointment to
  `completed`, on the *same* SQLAlchemy session — one `commit()` makes
  both changes atomic; if either write fails, `commit()`'s `rollback()`
  undoes both, so you never end up with a billing but no status update
  or vice versa. `Billing` also adds `CHECK` constraints
  (`consultation_fee >= 0`, `additional_charges >= 0`, `total_amount >=
  0`) in addition to the Pydantic-level validation, so the rule holds
  even for a row written outside the API.

### Billing flow (for the README/demo)
1. Register an admin and log in (see "Getting a working session" above).
2. Create a doctor and a patient under that doctor.
3. Optionally create an appointment for them (`POST /appointments`).
4. `POST /billings` with `patient_id`, `doctor_id`, `consultation_fee`,
   `additional_charges`, `payment_mode`, and optionally `appointment_id`.
   The response includes the computed `total_amount` and
   `payment_status: "pending"`. If you passed `appointment_id`, check
   `GET /appointments/{id}` — its status is now `completed`.
5. Mark it paid: `PATCH /billings/{id}` with `{"payment_status": "paid"}`.
6. Try billing the same `appointment_id` again — you'll get
   `duplicate_billing` (400).
7. `GET /reports/revenue?doctor_id=<id>` to see that doctor's total paid
   revenue, broken down by day.

## What's implemented (Levels 11-18)
- **11 - Roles:** `admin` / `doctor` on the `User` model, carried in the JWT.
  Admin has full access. A doctor login is linked to one `Doctor` row
  (`doctor_id` at registration) and can only view/edit their own patients
  and appointments; doctor/patient deletes are admin-only. Unauthorized
  actions return 403; missing/invalid tokens return 401.
- **12 - Appointments:** new `Appointment` model + full CRUD, plus
  `GET /doctors/{id}/appointments` and `GET /patients/{id}/appointments`.
  Doctor/patient existence and active-doctor checks reuse Level 2's logic.
  Overlap detection treats each appointment as a 30-minute slot and blocks
  a second booking for the same doctor in that window (cancelled
  appointments don't block).
- **13 - Integrity:** DB-level unique constraint on doctor email (was
  already enforced at the app layer too), FK constraints with
  `ondelete="RESTRICT"` (and SQLite's FK pragma turned on, since SQLite
  ignores FKs by default), and a single `commit()` helper that turns any
  `IntegrityError` into a `{"error": {...}}` JSON message instead of a raw
  DB traceback.
- **14 - Performance:** `selectinload` on appointment listings to avoid
  N+1 queries when doctor/patient are serialized, a composite index on
  `(doctor_id, appointment_date)` for the overlap check and doctor listing,
  pagination that counts and slices in SQL rather than in Python, and an
  `X-Process-Time-Ms` response header plus a log line on every request.
- **15 - Audit:** `created_at`/`updated_at` (auto-managed) and
  `created_by`/`updated_by` (the calling user's username) on Doctor,
  Patient and Appointment.
- **16 - Hardening:** every error path — business rules, HTTP/auth errors,
  Pydantic validation errors, and any unhandled exception — returns the
  same `{"error": {"code", "message"}}` shape. A basic in-memory sliding
  window rate limiter (100 requests/60s per IP by default, configurable
  via `.env`) returns 429 when exceeded.
- **17 - Testing:** `tests/test_advanced.py` adds role-restriction tests,
  appointment validation/overlap tests, integrity and audit tests, and a
  uniform-error-shape test. Coverage is enforced via `pytest.ini`.
- **18 - Docs:** request/response examples on every schema (see
  `schemas.py`'s `json_schema_extra` and `Field(examples=...)`), tag
  descriptions grouping the Swagger UI, and docstrings on the auth routes.
