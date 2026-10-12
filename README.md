# Telegram Bot

Initial production-ready project skeleton for a Python Telegram Bot using aiogram, FastAPI, httpx, Pydantic and JSON files later.

## Requirements

- Python 3.12+

## Setup

```bash
python -m venv .venv
```

Activate the virtual environment, then install dependencies:

```bash
pip install -r requirements.txt
```

Create the environment file:

```bash
cp .env.example .env
```

Set the values in `.env`:

```env
BOT_TOKEN=
ADMIN_IDS=
SAM_API_BASE=
SAM_API_KEY=
```

`ADMIN_IDS` is a comma-separated list of Telegram IDs.

## Run

```bash
python -m app.main
```

The current stage only initializes the Telegram bot and FastAPI application. No login, JSON storage logic, products, Sam Topup integration, points, channels, orders, admin panel, or purchase UI is implemented yet.

## Tests

```bash
pytest -q
```

## JSON Storage

The project uses JSON files under `data/` as the only persistence layer. The `JsonStorage` service in `app/services/json_storage.py` is the centralized access layer; handlers must not access JSON files directly.

Writes use an exclusive file lock, a temporary file, `fsync`, and `os.replace` for atomic replacement. Existing JSON files are never deleted by initialization.

## Telegram + Email Login

The current login flow uses Telegram ID as the account identifier and asks only new Telegram users for an email address.

- Existing Telegram ID: reuse the existing user; no email is requested.
- New Telegram ID: FSM asks for email.
- Email is stripped and case-folded, then validated with Pydantic `EmailStr`.
- An email already linked to another Telegram ID is rejected and is never moved.
- New users start with `points_balance = 0`.
- No OTP, SMTP, password, verification code, or email message is used.

## Required Channels

Required channel membership is isolated in `RequiredChannelService` and reads channel configuration through `RequiredChannelsRepository` from `data/required_channels.json`.

Membership is checked with Telegram `getChatMember`. Active required channels are checked; `creator`, `administrator`, and `member` count as membership, while `restricted` counts only when Telegram returns `is_member=true`. Telegram API failures are never treated as membership.

The verification callback is `required_channels:check`. Public channels with a username receive a Telegram link button; no invite link is generated for channels without a username.

## Points System — Phase 7

The points balance is stored in `users.json` under `points_balance`. New users start with `points_balance = 0`.

`PointsService` is the only service responsible for changing balances. It provides:

- `get_balance`
- `credit`
- `debit`

Only Telegram IDs present in `ADMIN_IDS` can perform credit/debit operations through the service. Normal users cannot modify their balance.

Every admin balance change creates a transaction in `data/balance_transactions.json` using `ADMIN_CREDIT` or `ADMIN_DEBIT`. Admin operations set `order_id` to `null` because they are not linked to an order.

Balance updates and transaction creation are performed through a multi-file locked `JsonStorage.atomic_update()` operation with rollback when a file write fails. Concurrent balance changes are serialized by the existing file-locking mechanism.

No purchase flow, payment gateway, points top-up method for users, or full admin panel is included in this phase.

## Phase 9 — Admin Panel

The admin panel is authorized server-side using only Telegram IDs from `ADMIN_IDS`. Inline button visibility is not treated as authorization.

Sections:
- Users: search, view, balance, credit/debit points, ban/unban.
- Products: list, sync from Sam Topup, enable/disable, set sale price points.
- Channels: add, list, activate/deactivate using the existing required-channel fields.
- Orders: list/search/details, including `delivered_data` for admins.
- Supplier: live Sam Topup balance via `GET /balance`.
- Statistics: values calculated only from the existing JSON files.

No payment method, purchase flow, or additional business metric was added.

## Phase 10 — Telegram User Interface

Added the Telegram user interface only, using the existing AuthService, RequiredChannelService, PointsService, and PurchaseService.

User flow:
- `/start` requests email for unregistered users.
- Registered users see the main menu.
- Required channels are checked before protected services.
- Products show only enabled products with a positive `sale_price_points`.
- Product details → quantity → confirmation → PurchaseService.
- Successful purchases show delivered data and supplier order ID.
- User orders are derived only from PURCHASE transactions belonging to the current user.
- Balance and account views are read-only.
- No points purchase/payment UI was added.
