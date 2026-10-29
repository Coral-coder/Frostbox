# Frostbox

Deep Home Assistant integration for the **Samsung Bespoke AI 4-Door French Door with AI Family Hub (RF90F29AEWAA)**:
fridge controls, food-safety monitoring, an inventory with expiry dates, inside cameras in **Apple Home**,
voice control through Assist/Siri, Plex hooks, and switches that appear **on the fridge's own screen**.

Frostbox isn't a separate app. Everything it adds lives inside Home Assistant: entities, a to-do list,
cameras, actions, voice commands and a dashboard. That means it works in Apple Home, the HA app, automations,
and on the Family Hub screen itself.

```
                    ┌──────────── Samsung cloud (SmartThings) ────────────┐
 RF90F29AEWAA ──────┤ status · commands · AI Vision Inside photos         │
 (32" Family Hub)   └───────┬───────────────────────────────▲─────────────┘
        ▲                   │ OAuth (reuses HA's core       │ virtual switches
        │ browser kiosk     │ SmartThings login)            │ (Party, Grocery, Vacation, Dinner)
        │                   ▼                               │
        │        ┌─────────────────────── Home Assistant OS ─────────────────────────┐
        └────────┤  custom_components/frostbox                                      │
                 │   sensors · switches · setpoints · FlexZone · cameras · to-do     │
                 │   door analytics · food-safety tracker · modes · inventory        │
                 │   actions: add_item, find_item, get_status, suggest_recipes …     │
                 │  packages/frostbox_assist.yaml   → Assist / Siri voice           │
                 │  packages/frostbox_homekit.yaml  → HomeKit Bridge → Apple Home   │
                 │  blueprints/…                    → alerts, Plex, calendar, zones │
                 │  dashboards/frostbox_fridge.yaml → kitchen screen                │
                 └───────────────────────────────────────────────────────────────────┘
```

## What you get

| Area | Entities / features |
| --- | --- |
| **Fridge** | AutoFill pitcher, Auto Open Door, door alarm, AI precise cooling, ice maker night mode, defrost delay, ice maker status, monthly energy, firmware-update flag, *take inside photos* button. Fridge / freezer / FlexZone temperatures and setpoints, FlexZone mode (Deli, Wine, Beverage…), Power Cool, Power Freeze, ice maker and Ice Bites switches, water filter %, deodorizer filter, power and energy (Energy dashboard ready), defrost, connectivity |
| **Doors** | Per-door contact sensors, *any door open*, **door ajar** (configurable delay), openings today, open time today, last opened, `frostbox_door` events |
| **Food safety** | `sensor.*_food_safety` = `ok / warning / unsafe` using USDA rules (fridge > 40 °F for 4 h, freezer thawed). Tracks outages, **including time when HA itself was down**. Stays `unsafe` until you acknowledge it and tells you what to throw out |
| **Inventory** | `todo.*_inventory`: every item is a to-do whose due date is its expiry date. Barcode lookup (Open Food Facts), automatic shelf life by category, leftovers, staples that re-add themselves to your shopping list, *expiring soon* / *expired* sensors |
| **Recipes** | `frostbox.suggest_recipes` ranks recipes by what expires first (TheMealDB, or any HA AI conversation agent you pick) |
| **Modes** | Party (Power Cool + Power Freeze + FlexZone → beverage, auto-off and restores the drawer), Grocery (pre-chill), Vacation. Each has a timer |
| **Cameras** | Inside-the-fridge camera(s) that **stream to Apple Home** like your other cameras (live view + snapshots + door-opening "motion") |
| **Fridge screen** | Kiosk page with **Aquarium** and **Matrix** screensavers that show fridge info and wake when a door opens. Optional SmartThings virtual switches *Frostbox Party Mode / Grocery Mode / Vacation Mode / Dinner Ready*. Tap them on the Family Hub or say "Hi Bixby, turn on Frostbox Party Mode", and HA reacts. Plus a full HA dashboard in the Family Hub browser |
| **Voice** | Assist sentences (also through Siri): "do we have eggs?", "what's expiring?", "add yogurt to the fridge", "we're out of milk", "what should I cook?", "start party mode", "dinner's ready" |
| **Plex** | Kitchen now-playing on the fridge screen, pause Plex while the fridge is held open, "Dinner's ready" turns on the TV and resumes Plex |
| **Power users** | `frostbox.send_command` (any SmartThings command), `frostbox.dump_capabilities`, a disabled-by-default diagnostic sensor for **every** attribute the fridge reports |

## Install (Home Assistant OS)

1. **SmartThings first.** Add the fridge in the SmartThings phone app, then in HA go to
   *Settings → Devices & services → Add integration → SmartThings* and sign in. Frostbox reuses that login,
   so there are no tokens or developer apps to manage. For a quick test you can also use a 24-hour PAT from
   <https://account.smartthings.com/tokens>.
2. **Install Frostbox.** In HACS go to *⋮ → Custom repositories*, add `https://github.com/coral-coder/frostbox`
   (type *Integration*), install, and restart. Without HACS, copy `custom_components/frostbox` into
   `/config/custom_components/` (the *File editor* or *Samba* add-on is easiest).
3. Go to *Settings → Devices & services → Add integration → Frostbox* and choose **Use the SmartThings integration**.
4. **Packages.** Copy `homeassistant/packages/*.yaml` to `/config/packages/` and make sure `configuration.yaml` has:
   ```yaml
   homeassistant:
     packages: !include_dir_named packages
   ```
5. **Blueprints.** Copy `homeassistant/blueprints/automation/frostbox/` to `/config/blueprints/automation/frostbox/`
   (or import each one by URL), then create automations from them under *Settings → Automations → Blueprints*.
6. **Dashboard.** Go to *Settings → Dashboards → Add dashboard → From scratch → ⋮ → Raw configuration editor*
   and paste `homeassistant/dashboards/frostbox_fridge.yaml`.

> **Entity IDs** in the packages and dashboard assume the fridge is called **"Family Hub"** in SmartThings
> (`sensor.family_hub_fridge_temperature`, …). Either rename it in the SmartThings app before adding Frostbox,
> or search-and-replace `family_hub` in those files.

## Options

Go to *Settings → Devices & services → Frostbox → Configure*:

- **Polling interval.** Default 20 s. When the core SmartThings integration is installed, Frostbox also refreshes
  immediately whenever SmartThings pushes a change, so doors react in about a second.
- **Door ajar after.** Default 60 s.
- **Warn before expiry.** Default 2 days.
- **Shopping list.** Low staples and similar items go here. Any to-do list works, including one synced with Apple
  Reminders.
- **Camera sources / View Inside URL.** See *Cameras* below.
- **Add Frostbox switches to the fridge screen.** Creates the SmartThings virtual switches.
- **Party / grocery length, FlexZone party mode, AI agent for recipes.**

## Cameras → Apple Home

The Family Hub's inside cameras take **still photos** (after a door closes). HomeKit only shows cameras that
stream video, so each Frostbox camera:

1. keeps the latest photo (refreshed periodically and about 20 s and 90 s after a door closes), and
2. serves it as a steady 10 fps MJPEG feed on HA's loopback, protected by a random per-camera token. Its
   `stream_source` is the ffmpeg input for that feed, which **HomeKit Bridge** passes straight to ffmpeg.

So **no add-on is needed**: pair the *Fridge Camera* accessory from `frostbox_homekit.yaml` and the fridge shows up
next to your other cameras, with live view and snapshots. Door openings are linked as motion, so Apple Home can
send you a snapshot notification.

**Where the photos come from**, in order of preference:

- **Camera sources**: any HA camera that already gets the fridge photos, for example a community Family Hub
  camera integration that handles Samsung's separate image token. Frostbox wraps it and makes it HomeKit-ready.
- **View Inside URL template** (advanced): Frostbox reads the photo file ids from `samsungce.viewInside` and
  downloads them itself. Samsung doesn't document this endpoint (and it may need a separate token), so the URL
  is configurable. Run `frostbox.dump_capabilities` on delivery day and we'll fill it in.

Until either is set, the camera shows a placeholder and its `setup` attribute says what's missing.

## Siri, Shortcuts and barcodes

- **Voice.** In the iOS Shortcuts app, create a shortcut named e.g. *"Fridge"* that uses the Home Assistant
  *Assist* action with *Ask each time* (dictated text). Then say "Hey Siri, Fridge… do we have eggs?" Every
  sentence in `frostbox_assist.yaml` works this way.
- **Barcode scanning.** Make a shortcut with *Scan QR/Barcode* → Home Assistant *Perform action*
  `frostbox.add_item` with `barcode: <Shortcut input>` → *Show result* `speech`. Add it to the Home Screen or
  Action button. Unknown barcodes are saved as "Item 0123…" so you can rename them.
- **Apple Home**: doors, temperatures, Power Cool/Freeze, modes and *Dinner Ready* are exposed through the
  *Frostbox* bridge, so they work in Siri and Home scenes ("Hey Siri, turn on Fridge Party Mode").

## Fridge screen kiosk

Open this in the Family Hub's browser and bookmark it:

```
http://homeassistant.local:8123/frostbox/kiosk.html?dashboard=/dashboard-frostbox/kitchen
```

It shows the Frostbox dashboard full-screen and switches to a **screensaver** after 3 minutes without a touch.
Tapping the screen, or opening any fridge door, wakes it straight back to the dashboard.

| Screensaver | |
| --- | --- |
| **Aquarium** | Clownfish, tangs, angelfish, neon tetras and a jellyfish, with swaying seaweed, rising bubbles and light rays. The tank lights dim at night, and the fish dart when someone opens the fridge |
| **Matrix** | Green code rain. Now and then a column spells something that's in your fridge |
| **Cycle** | Alternates between the two every 10 minutes |

Both screensavers show the time, fridge and freezer temperatures and what to use first. They also flag an open
door or a food-safety problem, and the info drifts slowly so nothing burns into the panel.

- **Pick one** with the `select.family_hub_screensaver` entity (Aquarium / Matrix / Cycle / Off). You can change it
  from your phone, an automation (Matrix at night?), or Apple Home.
- **Start one now** with `button.family_hub_start_screensaver`, or fire the event
  `frostbox_screensaver` (`{"action": "start", "saver": "matrix"}` or `{"action": "stop"}`).
- **URL options:** `idle=300` (seconds), `saver=matrix` (ignore the entity), `device=family_hub` (entity prefix).
- **Preview** either one on any screen: `/frostbox/aquarium.html` or `/frostbox/matrix.html`.

To skip the login on the fridge, give it a dedicated HA user and trust only the fridge's IP:

```yaml
homeassistant:
  auth_providers:
    - type: homeassistant
    - type: trusted_networks
      trusted_networks:
        - 192.168.1.50/32          # the fridge's IP (give it a DHCP reservation)
      trusted_users:
        192.168.1.50/32: <kiosk user id>   # Settings → People → Users (advanced mode) shows the id
      allow_bypass_login: true
```

With *Add Frostbox switches to the fridge screen* on, Party/Grocery/Vacation/Dinner also appear as native tiles in
the Family Hub's SmartThings panel and work with Bixby.

## Actions

| Action | What it does | Response |
| --- | --- | --- |
| `frostbox.add_item` | name or barcode, quantity, location, expiry/days, staple + min quantity | item, `speech` |
| `frostbox.add_leftover` | leftovers with a short shelf life | item, `speech` |
| `frostbox.consume_item` | use some or all; low staples → shopping list | remaining, `speech` |
| `frostbox.remove_item` | delete by name | removed, `speech` |
| `frostbox.find_item` | "do I have …" | items, `speech` |
| `frostbox.get_status` | temperatures, doors, safety, expiring, filter, modes | everything, `speech` |
| `frostbox.suggest_recipes` | recipes using what expires first | recipes, `speech` |
| `frostbox.activate_mode` | party / grocery / vacation on/off with hours | `speech` |
| `frostbox.send_command` | raw SmartThings command | none |
| `frostbox.dump_capabilities` | writes `/config/frostbox_capabilities.json` | path, capabilities |

Events: `frostbox_door` (open/close with duration), `frostbox_dinner_ready`, `frostbox_inventory_changed`.

## Blueprints

| Blueprint | Does |
| --- | --- |
| `door_ajar` | Push notification, then flash lights and announce on speakers; clears when the door closes |
| `food_safety` | Critical alert with what to throw out (warning → time-sensitive, unsafe → critical) |
| `expiring_digest` | Daily "use these first" with a recipe idea |
| `water_filter` | Adds the filter to the shopping list when it's due |
| `party_calendar` | Party mode N hours before calendar events matching keywords |
| `grocery_mode` | Pre-chill when you leave the grocery store zone |
| `vacation_sync` | Vacation mode follows an away/vacation helper |
| `plex_door_pause` | Pause Plex when the fridge is held open; resume on close |
| `dinner_ready` | Fridge button / Bixby / voice: notify everyone, announce, TV on, resume Plex |

## Delivery-day checklist

1. Set up the fridge on Wi-Fi in the SmartThings app (the setup usually wants 2.4 GHz) and give it a DHCP
   reservation.
2. Rename it **Family Hub** in SmartThings.
3. Add Frostbox, then run **Developer tools → Actions → `frostbox.dump_capabilities`**.
4. Send over `/config/frostbox_capabilities.json` (or the Frostbox *Download diagnostics* file). It's the real
   capability list for this model, and we'll map anything Frostbox doesn't cover yet: AutoFill pitcher, Ice Bites,
   Auto Open Door, the View Inside image URL.
5. Pair the two HomeKit bridges, open the dashboard on the fridge, and import the blueprints.

## What's verified and what isn't

- ✅ **Unit and integration tests (48)** run against Home Assistant 2026.2 with a simulated 4-door Family Hub:
  setup, every platform, commands, door ajar timing, party mode expiry/restore, outage → unsafe, inventory, barcode
  lookup, to-do completion, fridge-screen switch sync, the MJPEG feed, the config flow, every blueprint and
  package through HA's own validators, and voice commands end to end through Assist.
- ✅ The kiosk and both screensavers were run in Chromium at 1080×1920 and 1920×1080 against a mock dashboard (idle → screensaver, tap to wake, door-open wake, switching from the entity, alerts, no JS errors). The Family Hub's own browser hasn't been tested yet.
- ✅ The HomeKit camera input format was checked against real ffmpeg (6 s of wall time → 6.0 s of H.264 at the
  expected frame rate) and against HA's HomeKit and ha-ffmpeg source.
- ⚠️ The **simulated** device uses Samsung capability names known from other Family Hub models. The real
  RF90F29AEWAA may name some things differently. Anything it reports that isn't mapped still shows up as a
  diagnostic sensor, and the capabilities dump lets us map it quickly.
- ⚠️ **AI Vision Inside photos**: the download endpoint is undocumented (see *Cameras*).
- ⚠️ **Virtual switches** need the SmartThings token to allow creating devices. If Samsung refuses, Frostbox logs
  a warning and everything else keeps working.
- ⚠️ One (unconfirmed) report says SmartThings API access for individual developers may become a paid plan.
  Reusing the core integration's login is the path least likely to be affected.

## Development

```bash
python -m venv .venv && . .venv/bin/activate
pip install -r requirements_test.txt
ruff check . && pytest -q
```
