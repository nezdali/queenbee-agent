import json as _json


async def _llm_extract_filters(text: str, today_iso: str) -> dict:
    """Use the LLM to parse natural-language booking queries into structured filters.
    Returns a dict of strings (or empty dict on any failure).
    Keys are the same as the structured `key=value` tokens accepted by run()."""
    if not text or not text.strip():
        return {}
    try:
        from services.llm_service import client
        from config import LLM_MODEL
    except Exception:
        return {}

    system_prompt = (
        "You extract structured booking-search filters from a user's natural-language "
        "request (in any language: English, Russian, Estonian, etc.). "
        "Return a single JSON object with ONLY these optional string keys "
        "(omit keys when the user did not mention them):\n"
        "  destination       — city or country name (e.g. 'Monaco', 'Paris')\n"
        "  checkin           — YYYY-MM-DD\n"
        "  checkout          — YYYY-MM-DD\n"
        "  adults            — integer as string\n"
        "  children          — integer as string\n"
        "  rooms             — integer as string\n"
        "  price_min         — integer per-night, as string\n"
        "  price_max         — integer per-night, as string\n"
        "  currency          — 3-letter code (default EUR)\n"
        "  type              — one of: hotel, apartment, hostel, villa, resort, "
        "guesthouse, bnb, holiday_home, motel, glamping, camping, chalet, "
        "farm_stay, homestay, boat, lodge\n"
        "  stars             — comma-separated 1-5\n"
        "  facility          — comma-separated, from: parking, pets, bar, fitness, "
        "gym, pool, spa, sauna, restaurant, beachfront, terrace, garden, hot_tub, "
        "ev_charging, shuttle, family_rooms, non_smoking, accessible, jacuzzi\n"
        "  room              — comma-separated, from: ac, balcony, private_bathroom, "
        "tv, kitchen, kitchenette, sea_view, mountain_view, city_view, view, "
        "terrace, bath, washing_machine, soundproofing, heating\n"
        "  meal              — comma-separated, from: breakfast, half_board, "
        "full_board, all_inclusive, self_catering\n"
        "  free_cancel       — 'yes' if free cancellation requested\n"
        "  no_prepay         — 'yes' if no prepayment requested\n"
        "  sustainable       — 'yes' if eco/sustainable requested\n"
        "  sort              — price | review | distance | stars | top_reviewed | deals\n"
        "  amenities         — comma-separated Airbnb amenities, from: wifi, kitchen, "
        "parking, pool, hot_tub, ac, heating, washer, dryer, tv, breakfast, workspace, "
        "pets, gym, elevator, fireplace, wheelchair, crib, kid_friendly, ev_charger, "
        "beachfront, waterfront, lake_access, bathtub\n"
        "  bedrooms          — minimum bedrooms (integer as string)\n"
        "  beds              — minimum beds (integer as string)\n"
        "  bathrooms         — minimum bathrooms (integer as string)\n"
        "  instant_book      — 'yes' if user wants instant booking\n"
        "  superhost         — 'yes' if user wants only Superhosts\n"
        "  pets_allowed      — 'yes' if traveling with pets\n\n"
        f"TODAY: {today_iso}. Resolve relative dates: 'today', 'tomorrow', "
        "'this weekend', 'next weekend' (= the upcoming Sat-Sun pair AFTER this "
        "week's; if today is already a weekday Mon-Thu, 'next weekend' still means "
        "the very next Sat-Sun), 'in N days', 'next month'. Default trip duration: "
        "weekend = Sat checkin → Mon checkout (2 nights); otherwise 2 nights.\n"
        "Russian hints: 'квартира'='apartment', 'отель'/'гостиница'='hotel', "
        "'хостел'='hostel', 'вилла'='villa', 'вид на море'/'у моря'=sea_view, "
        "'с балконом'=balcony, 'кухня'=kitchen, 'бесплатная отмена'=free_cancel, "
        "'2 взрослых'/'2х взрослых'='adults=2', 'на 2 ночи'=2 nights, "
        "'с бассейном'='amenities=pool', 'с парковкой'='amenities=parking', "
        "'с кондиционером'='amenities=ac', 'с собакой'/'с питомцем'='pets_allowed=yes', "
        "'2 спальни'='bedrooms=2', 'суперхост'='superhost=yes', "
        "'мгновенное бронирование'='instant_book=yes', "
        "'для инвалидов'='amenities=wheelchair'.\n"
        "Estonian: 'korter'='apartment', 'hotell'='hotel', 'merevaatega'=sea_view, "
        "'parkimisega'='amenities=parking', 'basseiniga'='amenities=pool'.\n"
        "Output JSON ONLY, no commentary, no code fences."
    )
    try:
        kwargs = dict(
            model=LLM_MODEL,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": text},
            ],
            response_format={"type": "json_object"},
        )
        try:
            resp = await client.chat.completions.create(**kwargs, temperature=0)
        except Exception as exc_t:
            if "temperature" in str(exc_t).lower():
                resp = await client.chat.completions.create(**kwargs)
            else:
                raise
        raw = resp.choices[0].message.content or "{}"
        data = _json.loads(raw)
        if not isinstance(data, dict):
            return {}
        out = {}
        for k, v in data.items():
            if v in (None, "", [], {}):
                continue
            out[str(k)] = str(v)
        return out
    except Exception:
        return {}


import re as _re_avail


async def _check_availability(link: str, checkin: str, checkout: str,
                              adults: str, currency: str = "EUR") -> tuple[str, str]:
    """Best-effort availability check for a Booking.com or Airbnb listing URL.

    Returns (status, info) where status is one of:
      - 'available'    — strong positive signal (price found, no negative signal)
      - 'unavailable'  — strong negative signal in the page
      - 'unknown'      — could not determine (network error, JS-rendered, etc.)
    `info` is a short human-readable note (e.g. price snippet) or empty string.
    """
    try:
        from tool_utils import fetch_text
    except Exception:
        return ("unknown", "")

    # Build a date-aware URL
    if "booking.com/hotel/" in link:
        sep = "&" if "?" in link else "?"
        url = (
            f"{link}{sep}checkin={checkin}&checkout={checkout}"
            f"&group_adults={adults}&no_rooms=1&group_children=0"
            f"&selected_currency={currency}"
        )
    elif "airbnb.com/rooms/" in link or "airbnb.com/h/" in link:
        sep = "&" if "?" in link else "?"
        url = (
            f"{link}{sep}check_in={checkin}&check_out={checkout}&adults={adults}"
        )
    else:
        return ("unknown", "")

    text, status = await fetch_text(url, timeout=12)
    if not text or status != 200:
        return ("unknown", "")

    low = text.lower()

    # Negative signals
    BK_NEG = (
        "no rooms available",
        "we are sorry, but there is no availability",
        "the property no longer offers rooms",
        "извините, на ваши даты",
        "к сожалению, на выбранные даты",
        "sold out",
    )
    AB_NEG = (
        '"isavailable":false',
        '"unavailability"',
        "these dates aren't available",
        "the host has blocked these dates",
        "эти даты недоступны",
        "this place is no longer available",
    )

    if "booking.com/hotel/" in link:
        for pat in BK_NEG:
            if pat in low:
                return ("unavailable", "")
        # Positive: a price near the booking widget. Booking shows total like "€ 540"
        m = _re_avail.search(r"(?:€|eur|usd|\$|£)\s?[\d  .,]{2,7}", low)
        if m:
            price = m.group(0).strip().upper().replace("EUR", "€").replace("USD", "$")
            return ("available", price)
        return ("unknown", "")

    # Airbnb
    # Airbnb listing pages are heavily JS-rendered. The static HTML almost never
    # contains a reliable positive availability signal, so we ONLY emit
    # 'unavailable' when there is an explicit negative signal. Otherwise return
    # 'unknown' — never claim available, the user must verify on the site.
    for pat in AB_NEG:
        if pat in low:
            return ("unavailable", "")
    return ("unknown", "")


async def run(context: dict) -> str:
    import aiohttp
    from datetime import datetime, timedelta
    from urllib.parse import urlencode, quote_plus

    # ── Booking.com nflt filter code mappings ────────────────────────────

    PROPERTY_TYPES = {
        'hotel': 204, 'apartment': 201, 'hostel': 203, 'villa': 213,
        'resort': 206, 'guesthouse': 216, 'bnb': 208, 'holiday_home': 220,
        'motel': 205, 'glamping': 224, 'camping': 214, 'chalet': 209,
        'farm_stay': 210, 'homestay': 222, 'boat': 215, 'lodge': 221,
    }

    FACILITY_CODES = {
        'parking': 2, 'pets': 4, 'pet_friendly': 4, 'bar': 7,
        '24h_front_desk': 8, 'front_desk': 8, 'fitness': 11, 'gym': 11,
        'shuttle': 17, 'airport_shuttle': 17, 'family_rooms': 25,
        'non_smoking': 28, 'garden': 46, 'hot_tub': 47, 'jacuzzi': 47,
        'spa': 48, 'sauna': 49, 'indoor_pool': 54, 'restaurant': 107,
        'ev_charging': 301, 'pool': 433, 'swimming_pool': 433,
        'beachfront': 60, 'private_beach': 60, 'bbq': 100,
        'terrace': 66, 'shared_lounge': 84, 'laundry': 52,
        'accessible': 16, 'wheelchair': 16,
    }

    ROOM_FACILITY_CODES = {
        'ac': 11, 'air_conditioning': 11, 'balcony': 17,
        'private_bathroom': 38, 'tv': 81, 'flat_screen_tv': 81,
        'coffee_machine': 86, 'view': 93, 'electric_kettle': 123,
        'terrace': 126, 'sea_view': 143, 'mountain_view': 144,
        'city_view': 145, 'kitchen': 999, 'kitchenette': 999,
        'minibar': 6, 'safe': 15, 'desk': 21, 'hairdryer': 22,
        'washing_machine': 35, 'iron': 44, 'bath': 65, 'bathtub': 65,
        'soundproofing': 79, 'heating': 8,
    }

    MEAL_CODES = {
        'breakfast': 1, 'half_board': 3, 'full_board': 4,
        'all_inclusive': 7, 'self_catering': 9,
    }

    SORT_MAP = {
        'price': 'price', 'review': 'review_score_and_price',
        'distance': 'distance', 'stars': 'class',
        'top_reviewed': 'bayesian_review_score', 'deals': 'deals',
    }

    KNOWN_FLAGS = {'free_cancel', 'no_prepay', 'sustainable'}

    USAGE = (
        "🏨 Booking.com Search\n\n"
        "You can use natural language or structured filters:\n\n"
        "━━━ Natural language examples ━━━\n"
        "• find me an apartment in Paris for 2 people, June 20-26, up to 200 euro per night\n"
        "• cheap hotel in Barcelona with pool and breakfast, 4 stars, free cancellation\n"
        "• 5-star resort in Bali with spa for 2 adults and 1 child, July 1-10\n"
        "• hostel in Tallinn under 50 euro, August 5-8\n"
        "• villa in Crete with sea view and kitchen, sort by price\n\n"
        "━━━ Structured filter examples ━━━\n"
        "booking Tallinn\n"
        "booking Paris type=apartment checkin=2026-06-20 checkout=2026-06-26 adults=2 price_min=100 price_max=250\n"
        "booking Barcelona type=hotel stars=4,5 facility=pool,spa room=ac,sea_view meal=breakfast free_cancel sort=price\n\n"
        "━━━ Full example (all filters) ━━━\n"
        "booking Rome type=hotel checkin=2026-07-01 checkout=2026-07-07 adults=2 children=1 rooms=1 currency=EUR "
        "stars=4 price_min=80 price_max=200 min_review=8 facility=pool,parking room=ac,balcony,private_bathroom "
        "meal=breakfast free_cancel sort=price\n\n"
        "━━━ Filters ━━━\n\n"
        "📅 Dates & guests:\n"
        "  checkin=YYYY-MM-DD  checkout=YYYY-MM-DD\n"
        "  adults=N  children=N  rooms=N\n"
        "  currency=EUR|USD|GBP|SEK|NOK|...\n\n"
        "💰 Price (per night):\n"
        "  price_min=N  price_max=N\n\n"
        "⭐ Rating:\n"
        "  stars=3,4,5  min_review=8\n\n"
        "🏠 Property type (type=):\n"
        "  hotel, apartment, hostel, villa, resort,\n"
        "  guesthouse, bnb, holiday_home, motel,\n"
        "  glamping, camping, chalet, farm_stay,\n"
        "  homestay, boat, lodge\n\n"
        "🏊 Facilities (facility=, comma-separated):\n"
        "  parking, pets, bar, 24h_front_desk, fitness,\n"
        "  gym, shuttle, family_rooms, non_smoking,\n"
        "  garden, hot_tub, jacuzzi, spa, sauna,\n"
        "  indoor_pool, pool, restaurant, ev_charging,\n"
        "  beachfront, private_beach, bbq, terrace,\n"
        "  shared_lounge, laundry, accessible\n\n"
        "🛏 Room features (room=, comma-separated):\n"
        "  ac, balcony, private_bathroom, tv,\n"
        "  coffee_machine, view, electric_kettle,\n"
        "  terrace, sea_view, mountain_view, city_view,\n"
        "  kitchen, kitchenette, minibar, safe, desk,\n"
        "  hairdryer, washing_machine, iron, bath,\n"
        "  soundproofing, heating\n\n"
        "🍽 Meals (meal=, comma-separated):\n"
        "  breakfast, half_board, full_board,\n"
        "  all_inclusive, self_catering\n\n"
        "📋 Policies (just add the flag):\n"
        "  free_cancel — free cancellation\n"
        "  no_prepay — no prepayment needed\n"
        "  sustainable — eco-certified\n\n"
        "📊 Sort (sort=):\n"
        "  price, review, distance, stars,\n"
        "  top_reviewed, deals"
    )

    try:
        from tool_utils import fetch_json, parse_args
        import os

        args = parse_args(context)
        if not args:
            return USAGE

        # ── Parse arguments ──────────────────────────────────────────────

        joined = ' '.join(args)
        tokens = joined.split()
        params = {}
        destination_parts = []
        for tok in tokens:
            if '=' in tok:
                k, v = tok.split('=', 1)
                params[k.strip().lower()] = v.strip()
            elif tok.lower() in KNOWN_FLAGS:
                params[tok.lower()] = 'yes'
            else:
                destination_parts.append(tok)

        # ── Natural-language enrichment via LLM ───────────────────────────
        # If the message contains free-form text (not just `key=value` tokens
        # plus a short city name), ask the LLM to extract structured filters.
        # Explicit `key=value` tokens always win over LLM output.
        explicit_keys = set(params.keys())
        looks_natural = (
            len(destination_parts) >= 3
            or any(not p.isascii() for p in destination_parts)
        )
        if looks_natural:
            today_iso = datetime.utcnow().date().isoformat()
            llm_filters = await _llm_extract_filters(joined, today_iso)
            if llm_filters:
                # Merge LLM output into params for any key NOT explicitly given.
                for k, v in llm_filters.items():
                    if k == 'destination':
                        continue  # handled below
                    if k not in explicit_keys:
                        params[k] = v
                # Replace destination_parts with LLM destination if user did not
                # provide an explicit `destination=` token.
                llm_dest = llm_filters.get('destination', '').strip()
                if llm_dest and 'destination' not in explicit_keys:
                    destination_parts = [llm_dest]

        destination = params.get('destination') or ' '.join(destination_parts).strip()
        if not destination:
            return 'Please provide a destination, e.g. /booking Paris checkin=2026-07-01 checkout=2026-07-04 adults=2'

        today = datetime.utcnow().date()
        checkin = params.get('checkin', str(today + timedelta(days=7)))
        checkout = params.get('checkout', str(today + timedelta(days=9)))
        adults = params.get('adults', '2')
        children = params.get('children', '0')
        rooms = params.get('rooms', '1')
        price_min = params.get('price_min')
        price_max = params.get('price_max')
        stars = params.get('stars')
        min_review = params.get('min_review')
        currency = params.get('currency', 'EUR').upper()
        sort = params.get('sort', 'review')
        prop_type = params.get('type')
        facility = params.get('facility')
        room = params.get('room')
        meal = params.get('meal')
        free_cancel = params.get('free_cancel')
        no_prepay = params.get('no_prepay')
        sustainable = params.get('sustainable')
        # Airbnb-only filters
        amenities = params.get('amenities')
        bedrooms = params.get('bedrooms')
        beds = params.get('beds')
        bathrooms = params.get('bathrooms')
        instant_book = params.get('instant_book')
        superhost = params.get('superhost')
        pets_allowed = params.get('pets_allowed')

        try:
            datetime.strptime(checkin, '%Y-%m-%d')
            datetime.strptime(checkout, '%Y-%m-%d')
        except Exception:
            return 'Invalid date format. Use checkin=YYYY-MM-DD and checkout=YYYY-MM-DD'

        # ── Build Booking.com nflt filter string ─────────────────────────

        nflt_parts = []

        if stars:
            for s in stars.split(','):
                s = s.strip()
                if s:
                    nflt_parts.append(f'class={s}')

        if min_review:
            try:
                score = int(float(min_review) * 10)
                nflt_parts.append(f'review_score={score}')
            except ValueError:
                pass

        if price_min or price_max:
            pmin = price_min or '0'
            pmax = price_max or '9999'
            nflt_parts.append(f'price={currency}-{pmin}-{pmax}-1')

        if prop_type:
            for t in prop_type.split(','):
                t = t.strip().lower()
                if t in PROPERTY_TYPES:
                    nflt_parts.append(f'ht_id={PROPERTY_TYPES[t]}')

        if facility:
            for f in facility.split(','):
                f = f.strip().lower()
                if f in FACILITY_CODES:
                    nflt_parts.append(f'hotelfacility={FACILITY_CODES[f]}')

        if room:
            for r in room.split(','):
                r = r.strip().lower()
                if r in ROOM_FACILITY_CODES:
                    nflt_parts.append(f'roomfacility={ROOM_FACILITY_CODES[r]}')

        if meal:
            for m in meal.split(','):
                m = m.strip().lower()
                if m in MEAL_CODES:
                    nflt_parts.append(f'mealplan={MEAL_CODES[m]}')

        if free_cancel:
            nflt_parts.append('fc=2')

        if no_prepay:
            nflt_parts.append('cot=2')

        if sustainable:
            nflt_parts.append('tfl=1')

        nflt_str = ';'.join(nflt_parts)
        order = SORT_MAP.get(sort.lower(), 'review_score_and_price') if sort else None

        # ── Geocode destination ──────────────────────────────────────────

        country = None
        try:
            geo_data, geo_st = await fetch_json(
                'https://geocoding-api.open-meteo.com/v1/search',
                params={'name': destination, 'count': 1, 'language': 'en', 'format': 'json'},
                timeout=10,
            )
            if geo_data and geo_st == 200:
                geo_results = geo_data.get('results') or []
                if geo_results:
                    country = geo_results[0].get('country')
        except Exception:
            pass

        full_dest = f"{destination}, {country}" if country else destination

        # ── Calculate nights ─────────────────────────────────────────────

        try:
            nights = max((datetime.strptime(checkout, '%Y-%m-%d') - datetime.strptime(checkin, '%Y-%m-%d')).days, 1)
        except Exception:
            nights = 1

        # ── Build platform links ─────────────────────────────────────────

        # Booking.com (full filter support)
        bk_q = {
            'ss': destination, 'checkin': checkin, 'checkout': checkout,
            'group_adults': adults, 'no_rooms': rooms,
            'group_children': children, 'selected_currency': currency,
        }
        if order:
            bk_q['order'] = order
        booking_link = 'https://www.booking.com/searchresults.html?' + urlencode(bk_q, quote_via=quote_plus)
        if nflt_str:
            booking_link += '&nflt=' + nflt_str

        # Airbnb — carry as many filters as the public URL accepts
        # Stable Airbnb /s/{loc}/homes URL params:
        #   query, checkin, checkout, adults, children, infants, pets,
        #   price_min, price_max, room_types[], min_bedrooms, min_beds,
        #   min_bathrooms, amenities[]=ID, superhost=true, instant_book=true.
        ab_query = full_dest
        if room and any(rv in room.lower() for rv in ('sea_view', 'beachfront')):
            ab_query = f"{ab_query} sea view"
        elif room and 'mountain_view' in room.lower():
            ab_query = f"{ab_query} mountain view"

        # Amenity name → Airbnb numeric ID (stable in public URL).
        AB_AMENITY_IDS = {
            'wifi': 1, 'kitchen': 4, 'parking': 5, 'pool': 7, 'hot_tub': 8,
            'jacuzzi': 8, 'ac': 9, 'air_conditioning': 9, 'heating': 12,
            'washer': 21, 'washing_machine': 21, 'dryer': 22, 'tv': 25,
            'breakfast': 27, 'workspace': 30, 'pets': 33, 'pets_allowed': 33,
            'gym': 36, 'fitness': 36, 'elevator': 37, 'fireplace': 41,
            'wheelchair': 53, 'accessible': 53, 'crib': 56, 'kid_friendly': 57,
            'family_friendly': 57, 'ev_charger': 64, 'beachfront': 77,
            'waterfront': 78, 'lake_access': 89, 'bathtub': 257, 'bath': 257,
        }

        ab_params: list[tuple[str, str]] = [
            ('query', ab_query),
            ('checkin', checkin),
            ('checkout', checkout),
            ('adults', adults),
        ]
        if children and children != '0':
            ab_params.append(('children', children))
        if price_min:
            ab_params.append(('price_min', price_min))
        if price_max:
            ab_params.append(('price_max', price_max))
        if prop_type:
            pt = prop_type.lower()
            if pt in ('apartment', 'villa', 'holiday_home', 'chalet'):
                ab_params.append(('room_types[]', 'Entire home/apt'))
            elif pt in ('hostel', 'guesthouse', 'bnb', 'homestay'):
                ab_params.append(('room_types[]', 'Private room'))
            elif pt == 'hotel':
                ab_params.append(('room_types[]', 'Hotel room'))
        # min_bedrooms: prefer explicit `bedrooms=`, fall back to rooms when >1.
        if bedrooms and bedrooms.isdigit():
            ab_params.append(('min_bedrooms', bedrooms))
        elif rooms and rooms.isdigit() and int(rooms) > 1:
            ab_params.append(('min_bedrooms', rooms))
        if beds and beds.isdigit():
            ab_params.append(('min_beds', beds))
        if bathrooms and bathrooms.isdigit():
            ab_params.append(('min_bathrooms', bathrooms))
        if instant_book:
            ab_params.append(('instant_book', 'true'))
        if superhost:
            ab_params.append(('superhost', 'true'))
        if pets_allowed:
            ab_params.append(('pets', '1'))
        # Map booking facility/room → Airbnb amenity IDs (so e.g. `facility=pool`
        # also adds the Airbnb pool filter).
        amenity_sources = []
        if amenities:
            amenity_sources.append(amenities)
        if facility:
            amenity_sources.append(facility)
        if room:
            amenity_sources.append(room)
        seen_ids: set[int] = set()
        for src in amenity_sources:
            for tok in src.split(','):
                aid = AB_AMENITY_IDS.get(tok.strip().lower())
                if aid and aid not in seen_ids:
                    seen_ids.add(aid)
                    ab_params.append(('amenities[]', str(aid)))

        airbnb_link = (
            'https://www.airbnb.com/s/' + quote_plus(full_dest)
            + '/homes?' + urlencode(ab_params, quote_via=quote_plus)
        )

        # Expedia
        expedia_link = 'https://www.expedia.com/Hotel-Search?' + urlencode({
            'destination': full_dest, 'startDate': checkin, 'endDate': checkout,
            'adults': adults, 'rooms': rooms,
        }, quote_via=quote_plus)

        # Hotels.com
        hotels_link = 'https://www.hotels.com/Hotel-Search?' + urlencode({
            'destination': full_dest, 'startDate': checkin, 'endDate': checkout,
            'adults': adults, 'rooms': rooms,
        }, quote_via=quote_plus)

        # Agoda
        agoda_link = (
            f"https://www.agoda.com/search?q={quote_plus(full_dest)}"
            f"&checkIn={checkin}&checkOut={checkout}"
            f"&rooms={rooms}&adults={adults}&children={children}"
            f"&priceCur={currency}"
        )

        # ── SerpAPI: Google Hotels (top 5) ───────────────────────────────

        api_key = os.getenv("SERPAPI_KEY", "")
        booking_results = []
        airbnb_results = []

        if api_key:
            # Google Search for Booking.com hotel pages
            bk_q_parts = ["site:booking.com/hotel", destination]
            if prop_type:
                bk_q_parts.append(prop_type)
            if room and "sea_view" in room.lower():
                bk_q_parts.append("sea view")
            booking_search_q = " ".join(bk_q_parts)
            bk_data, bk_st = await fetch_json(
                "https://serpapi.com/search.json",
                params={
                    "engine": "google", "q": booking_search_q,
                    "gl": "ee", "hl": "en", "num": 10, "api_key": api_key,
                },
                timeout=15,
            )
            if bk_data and bk_st == 200:
                for r in (bk_data.get("organic_results") or []):
                    link = r.get("link", "")
                    if "booking.com/hotel/" in link and len(booking_results) < 3:
                        booking_results.append(r)

            # Google Search for Airbnb listings
            ab_q_parts = ["site:airbnb.com", destination]
            if prop_type:
                ab_q_parts.append(prop_type)
            if room and "sea_view" in room.lower():
                ab_q_parts.append("sea view")
            airbnb_query = " ".join(ab_q_parts)
            ab_data, ab_st = await fetch_json(
                "https://serpapi.com/search.json",
                params={
                    "engine": "google", "q": airbnb_query,
                    "gl": "ee", "hl": "en", "num": 10, "api_key": api_key,
                },
                timeout=15,
            )
            if ab_data and ab_st == 200:
                # Accept both /rooms/ and /h/ (legacy listing URLs)
                for r in (ab_data.get("organic_results") or []):
                    link = r.get("link", "")
                    if any(p in link for p in ("airbnb.com/rooms/", "airbnb.com/h/")) \
                            and len(airbnb_results) < 3:
                        airbnb_results.append(r)

        # ── Best-effort availability check for top 3 in each platform ────

        import asyncio as _asyncio_avail
        availability: dict[str, tuple[str, str]] = {}
        check_targets: list[str] = []
        for r in booking_results + airbnb_results:
            link = r.get("link", "")
            if link:
                check_targets.append(link)
        if check_targets:
            try:
                results = await _asyncio_avail.gather(
                    *[_check_availability(u, checkin, checkout, adults, currency)
                      for u in check_targets],
                    return_exceptions=True,
                )
                for url, res in zip(check_targets, results):
                    if isinstance(res, tuple) and len(res) == 2:
                        availability[url] = res
                    else:
                        availability[url] = ("unknown", "")
            except Exception:
                pass

        def _avail_badge(link: str) -> str:
            st, info = availability.get(link, ("unknown", ""))
            if st == "available":
                return f" ✅ available{(' ' + info) if info else ''}"
            if st == "unavailable":
                return " ❌ not available for these dates"
            return " ❓ availability unknown — check on site"

        # ── Format output ────────────────────────────────────────────────

        lines = []
        lines.append("🏨 *Accommodation search*")
        lines.append("━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
        title = f"📍 *{destination}*"
        if country:
            title += f", {country}"
        lines.append(title)
        lines.append(f"📅 *{checkin} → {checkout}* ({nights} night{'s' if nights != 1 else ''})")
        lines.append(f"👤 {adults} adult{'s' if adults != '1' else ''}"
                     + (f", {children} child{'ren' if children != '1' else ''}" if children and children != '0' else '')
                     + f"  |  🚪 {rooms} room{'s' if rooms != '1' else ''}  |  💱 {currency}")

        active = []
        if price_min or price_max:
            ps = "💰 "
            if price_min and price_max:
                ps += f"*{price_min}–{price_max} {currency}*/night"
            elif price_min:
                ps += f"from *{price_min} {currency}*/night"
            else:
                ps += f"up to *{price_max} {currency}*/night"
            active.append(ps)
        if stars:
            active.append(f"⭐ {stars} stars")
        if min_review:
            active.append(f"📊 review ≥ {min_review}")
        if prop_type:
            active.append(f"🏠 {prop_type}")
        if facility:
            active.append(f"🏊 {facility}")
        if room:
            active.append(f"🛏 {room}")
        if meal:
            active.append(f"🍽 {meal}")
        if amenities:
            active.append(f"✨ {amenities}")
        if bedrooms:
            active.append(f"🛌 ≥{bedrooms} bedrooms")
        if beds:
            active.append(f"🛏 ≥{beds} beds")
        if bathrooms:
            active.append(f"🛁 ≥{bathrooms} bathrooms")
        if instant_book:
            active.append("⚡ instant book")
        if superhost:
            active.append("🏅 Superhost only")
        if pets_allowed:
            active.append("🐶 pets allowed")
        if free_cancel:
            active.append("✅ free cancellation")
        if no_prepay:
            active.append("💳 no prepayment")
        if sustainable:
            active.append("🌿 eco-certified")
        if active:
            lines.append('')
            lines.append('🔍 *Filters:*')
            for a in active:
                lines.append(f"  {a}")

        # ── Top 3 Booking.com listings (via Google site search) ──────────

        lines.append('')
        lines.append('━━━━━━━━━━━━━━━━━━━━━━━━━━━━')
        if booking_results:
            lines.append('🔵 *Top 3 Booking.com*')
            lines.append('')
            for i, r in enumerate(booking_results, 1):
                t = r.get("title", "?").strip()
                snippet = r.get("snippet", "").strip()
                link = r.get("link", "")
                if len(t) > 80:
                    t = t[:77] + "..."
                if len(snippet) > 140:
                    snippet = snippet[:137] + "..."
                lines.append(f"*{i}. {t}*")
                if snippet:
                    lines.append(f"  📝 _{snippet}_")
                if link:
                    lines.append(f"  {_avail_badge(link)}")
                    lines.append(f"  🔗 [Open on Booking.com]({link})")
                lines.append('')

        # ── Top 3 Airbnb ───────────────────────────────────────────────────────────────

        lines.append('━━━━━━━━━━━━━━━━━━━━━━━━━━━━')
        if airbnb_results:
            lines.append('🩷 *Top 3 Airbnb*')
            lines.append('')
            for i, r in enumerate(airbnb_results, 1):
                t = r.get("title", "?").strip()
                snippet = r.get("snippet", "").strip()
                link = r.get("link", "")
                if len(t) > 80:
                    t = t[:77] + "..."
                if len(snippet) > 140:
                    snippet = snippet[:137] + "..."
                lines.append(f"*{i}. {t}*")
                if snippet:
                    lines.append(f"  📝 _{snippet}_")
                if link:
                    lines.append(f"  {_avail_badge(link)}")
                    lines.append(f"  🔗 [Open on Airbnb]({link})")
                lines.append('')

        # ── Common search links (all platforms) ──────────────────────────

        lines.append('━━━━━━━━━━━━━━━━━━━━━━━━━━━━')
        lines.append('🌐 *Search with the same filters on:*')
        lines.append(f'  🔵 [Booking.com]({booking_link})')
        lines.append(f'  🩷 [Airbnb]({airbnb_link})')
        lines.append(f'  🟡 [Expedia]({expedia_link})')
        lines.append(f'  🔴 [Hotels.com]({hotels_link})')
        lines.append(f'  🟢 [Agoda]({agoda_link})')

        lines.append('')
        lines.append('💡 _Send `/booking` to see all available filters_')

        return '\n'.join(lines)
    except Exception as e:
        return f"Booking search failed: {str(e)}"
