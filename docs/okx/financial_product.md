# Financial Product

## On-chain Earn

### REST API

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/api/v5/finance/staking-defi/offers` | Get offers |
| POST | `/api/v5/finance/staking-defi/purchase` | Purchase |
| POST | `/api/v5/finance/staking-defi/redeem` | Redeem |
| POST | `/api/v5/finance/staking-defi/cancel` | Cancel purchases/redemptions |
| GET | `/api/v5/finance/staking-defi/orders-active` | Get active orders |
| GET | `/api/v5/finance/staking-defi/orders-history` | Get order history |

---

## ETH Staking

### REST API

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/api/v5/finance/staking-defi/eth/product-info` | Get product info |
| POST | `/api/v5/finance/staking-defi/eth/purchase` | Purchase |
| POST | `/api/v5/finance/staking-defi/eth/redeem` | Redeem |
| POST | `/api/v5/finance/staking-defi/eth/cancel-redeem` | Cancel redeem |
| GET | `/api/v5/finance/staking-defi/eth/balance` | Get balance |
| GET | `/api/v5/finance/staking-defi/eth/purchase-redeem-history` | Get purchase & redeem history |
| GET | `/api/v5/finance/staking-defi/eth/apy-history` | Get APY history (public) |

---

## SOL Staking

### REST API

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/api/v5/finance/staking-defi/sol/product-info` | Get product info |
| POST | `/api/v5/finance/staking-defi/sol/purchase` | Purchase |
| POST | `/api/v5/finance/staking-defi/sol/redeem` | Redeem |
| GET | `/api/v5/finance/staking-defi/sol/balance` | Get balance |
| GET | `/api/v5/finance/staking-defi/sol/purchase-redeem-history` | Get purchase & redeem history |
| GET | `/api/v5/finance/staking-defi/sol/apy-history` | Get APY history (public) |

---

## Simple Earn Flexible

### REST API

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/api/v5/finance/savings/balance` | Get saving balance |
| POST | `/api/v5/finance/savings/purchase-redempt` | Savings purchase/redemption |
| POST | `/api/v5/finance/savings/set-lending-rate` | Set lending rate |
| GET | `/api/v5/finance/savings/lending-history` | Get lending history |
| GET | `/api/v5/finance/savings/lending-rate-summary` | Get public borrow info (public) |
| GET | `/api/v5/finance/savings/lending-rate-history` | Get public borrow history (public) |

---

## Flexible Loan

### REST API

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/api/v5/finance/flexible-loan/borrow-currencies` | Get borrowable currencies |
| GET | `/api/v5/finance/flexible-loan/collateral-assets` | Get collateral assets |
| POST | `/api/v5/finance/flexible-loan/max-loan` | Maximum loan amount |
| GET | `/api/v5/finance/flexible-loan/max-collateral-redeem` | Maximum collateral redeem amount |
| POST | `/api/v5/finance/flexible-loan/adjust-collateral` | Adjust collateral |
| GET | `/api/v5/finance/flexible-loan/loan-info` | Get loan info |
| GET | `/api/v5/finance/flexible-loan/loan-history` | Get loan history |
| GET | `/api/v5/finance/flexible-loan/accrued-interest` | Get accrued interest |
