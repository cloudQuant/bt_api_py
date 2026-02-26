# Affiliate

## REST API

### GET / Get the invitee's detail

Retrieve the invitee's detail.

- **Rate Limit**: 20 requests per 2 seconds
- **Rate limit rule**: User ID
- **Permission**: Read

```
GET /api/v5/affiliate/invitee/detail
```

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| uid | String | Yes | The invitee's User ID |

Response includes:
- Invitee's registration time
- Trading volume
- Commission earned
- Rebate details
