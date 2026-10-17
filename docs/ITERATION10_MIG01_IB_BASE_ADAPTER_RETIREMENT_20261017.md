# MIG01 IB and Base adapter retirement checkpoint

This parent round records two already accepted child commits and updates only
their gitlinks. It does not complete MIG01 or remove the remaining Base gateway
runtime tree.

- `bt_api/bt_api_ib_web` points to `2ac97b7077326bf5b6fddfba5fdb399d0976e3d0`
  on `dev`. That child round removed the IB gateway adapter package and its
  adapter-only tests while retaining the direct IB plugin/feed path.
- `bt_api/bt_api_base` points to `2e256869bc6deadebbedef135b373c3314b20ef7`
  on `dev`. That child round retires the Base lazy export for the IB adapter and
  updates its retirement test.

The parent change is limited to these two gitlinks and this record. The Base
gateway runtime deletion and packaging work is a separate, still-unaccepted
round; no formal MIG01 completion is claimed here.
