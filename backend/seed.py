# -*- coding: utf-8 -*-
"""Seed contacts ported from Bob's original CRM component.

This is the same data set the artifact shipped with, so first run mirrors the
current behavior. The original had one malformed entry (a contact missing its
id); it has been reconstructed here as the Fordyce Construction bid desk (c22).
"""

SEED_CONTACTS = [
    {
        "id": "c1", "name": "Corky Sigurdson", "company": "Sharp Memorial connection",
        "role": "Facilities relationship / connector", "email": "corkysigurdson@icloud.com",
        "phone": "", "category": "bd",
        "nextAction": "Coffee with Joe (Boys to Men) Tue 6/16 8:30am — Sheldon's, La Mesa. Thank Corky for the intro.",
        "nextDue": "2026-06-16",
        "notes": "Walked Sharp Memorial campus with Salam 6/11. Key door-opener for healthcare work. Also connected Bob to Joe at Boys to Men Mentoring — 100 Wave Challenge surf event, major SD charity. Corky is building Bob's community network, not just his BD network.",
        "log": [
            {"date": "2026-06-13", "note": "Corky sent Boys to Men 100 Wave Challenge intro email — connected Bob to Joe at boystomen.org."},
            {"date": "2026-06-13", "note": "Bob sent thank-you to Corky for the BTM intro."},
            {"date": "2026-06-11", "note": "Sharp Memorial campus walk with Salam, Carly. Great intros."},
            {"date": "2026-06-12", "note": "Sent thank-you follow-up. Salam called it a good email."},
        ],
    },
    {
        "id": "c2", "name": "Carly Bloom", "company": "ATR Mechanical Solutions",
        "role": "BD partner / connector", "email": "cbloom@atrmechanicalsolutions.com",
        "phone": "", "category": "bd", "nextAction": "", "nextDue": "",
        "notes": "Organized the Rady's lunch at King's Fish House 6/12. Strong connector between Star and the healthcare accounts.",
        "log": [
            {"date": "2026-06-12", "note": "Lunch with Rady's group, King's Fish House."},
            {"date": "2026-06-11", "note": "Met at Sharp Memorial walk."},
        ],
    },
    {
        "id": "c3", "name": "Letty Amador", "company": "Rady Children's Hospital",
        "role": "Contact", "email": "lamador@rchsd.org", "phone": "", "category": "bd",
        "nextAction": "Send intro follow-up after lunch", "nextDue": "2026-06-16",
        "notes": "Met at Rady's lunch 6/12. Healthcare target account.",
        "log": [{"date": "2026-06-12", "note": "Lunch at King's Fish House with Salam, Carly, Corky."}],
    },
    {
        "id": "c4", "name": "T. Ellis", "company": "Rady Children's Hospital",
        "role": "Contact", "email": "tellis1@rchsd.org", "phone": "", "category": "bd",
        "nextAction": "Send intro follow-up after lunch", "nextDue": "2026-06-16",
        "notes": "Met at Rady's lunch 6/12.",
        "log": [{"date": "2026-06-12", "note": "Lunch at King's Fish House."}],
    },
    {
        "id": "c5", "name": "Joaquin Hernandez", "company": "Caster Properties",
        "role": "Leasing", "email": "jhernandez@castergrp.com", "phone": "(619) 287-8873 x125",
        "category": "property",
        "nextAction": "4620 crane lift Wed 6/17 — be aware, coordinate if needed", "nextDue": "2026-06-17",
        "notes": "4620 industrial space lease. Crane lift machine on-site Wednesday 6/17 per property manager Hayley Acosta. Move is in motion.",
        "log": [
            {"date": "2026-06-15", "note": "Crane lift scheduled for 4620 Alvarado Canyon Rd, Wed 6/17."},
            {"date": "2026-06-12", "note": "Salam confirmed signing pending written condition guarantee."},
        ],
    },
    {
        "id": "c6", "name": "Maria Frausto Chavez", "company": "Caster Properties",
        "role": "Leasing", "email": "mfraustochavez@castergrp.com", "phone": "", "category": "property",
        "nextAction": "Lunch with Salam + Mr. Caster when he's back", "nextDue": "",
        "notes": "4620 lease. Salam wants to buy her, Mr. Caster, and me lunch.",
        "log": [],
    },
    {
        "id": "c7", "name": "Anthony Tasker", "company": "iCON National", "role": "GC contact",
        "email": "", "phone": "", "category": "gc",
        "nextAction": "Request 15–20 min debrief on Park at Fort Bend loss", "nextDue": "2026-06-17",
        "notes": "Park at Fort Bend bid — didn't land it. Salam requested a feedback call. Keep relationship warm for next round.",
        "log": [{"date": "2026-06-09", "note": "Bid result came back — not selected. Debrief requested."}],
    },
    {
        "id": "c8", "name": "Kaiden Cook", "company": "iCON National", "role": "GC contact",
        "email": "", "phone": "", "category": "gc", "nextAction": "", "nextDue": "", "notes": "", "log": [],
    },
    {
        "id": "c9", "name": "Dan Kelly", "company": "RCC Associates", "role": "Project Manager",
        "email": "dkelly@rccassociates.com", "phone": "954-429-3700 O / 954-254-5055 C", "category": "gc",
        "nextAction": "", "nextDue": "",
        "notes": "Deerfield Beach, FL office. Active on Baypointe basement bathroom tile. Responsive — 'Great news Bob, thank you' on extra box resolution 6/15.",
        "log": [{"date": "2026-06-15", "note": "Resolved extra tile boxes for Baypointe basement bathrooms. Good exchange."}],
    },
    {
        "id": "c10", "name": "Tina", "company": "Hardesty Associates", "role": "GC contact",
        "email": "", "phone": "", "category": "gc", "nextAction": "", "nextDue": "", "notes": "", "log": [],
    },
    {
        "id": "c11", "name": "Laura", "company": "Proform Interiors", "role": "GC contact",
        "email": "", "phone": "", "category": "gc", "nextAction": "", "nextDue": "", "notes": "", "log": [],
    },
    {
        "id": "c12", "name": "Julie Creed", "company": "CM Corp", "role": "GC contact",
        "email": "", "phone": "", "category": "gc",
        "nextAction": "Check status on European Wax Center bid (submitted 6/9)", "nextDue": "2026-06-18",
        "notes": "EWC Mission Valley bid due 6/9 — submitted. Follow up if no word.", "log": [],
    },
    {
        "id": "c13", "name": "Elena Stanton-Schultz", "company": "Floor & Decor", "role": "Vendor rep",
        "email": "", "phone": "", "category": "vendor", "nextAction": "", "nextDue": "",
        "notes": "Recurring across jobs.", "log": [],
    },
    {
        "id": "c14", "name": "Eric Alvarado", "company": "Daltile", "role": "Sales rep",
        "email": "eric.alvarado@daltile.com", "phone": "", "category": "vendor", "nextAction": "", "nextDue": "",
        "notes": "Most active tile vendor. Quoted Starling, Wax Co SD, two-job wall tile pricing. Natural Hues is made-to-order, 8–12 week lead.",
        "log": [{"date": "2026-06-05", "note": "Quote Q147025290 — two jobs, Daltile wall tile."}],
    },
    {
        "id": "c15", "name": "Joel Snook", "company": "Tom Duffy", "role": "Vendor rep",
        "email": "", "phone": "", "category": "vendor", "nextAction": "", "nextDue": "", "notes": "", "log": [],
    },
    {
        "id": "c16", "name": "Ken Olsen", "company": "Big D Supply", "role": "Vendor rep",
        "email": "", "phone": "", "category": "vendor", "nextAction": "", "nextDue": "", "notes": "", "log": [],
    },
    {
        "id": "c17", "name": "Stephanie Ennis", "company": "Interface", "role": "Sales rep",
        "email": "stephanie.ennis@interface.com", "phone": "", "category": "vendor", "nextAction": "", "nextDue": "",
        "notes": "Cove base (Apple Carlsbad — NH stock, ~2 wks to CA) and Environcare ED healthcare sheet ($13.05/sf) for Sharp Grossmont MRI.",
        "log": [{"date": "2026-05-27", "note": "Environcare ED pricing + install guide for Sharp Grossmont MRI."}],
    },
    {
        "id": "c18", "name": "DeSoto Sales (SD)", "company": "DeSoto Sales / UZIN", "role": "Distributor",
        "email": "sd@desotosales.com", "phone": "", "category": "vendor",
        "nextAction": "Review UZIN price list + pump program for SL pours", "nextDue": "2026-06-16",
        "notes": "UZIN price list received 6/12. Pump program can be free depending on SL pour volume — relevant for floor-leveling scopes like Zuma.",
        "log": [{"date": "2026-06-12", "note": "Sent UZIN price list + pump program details."}],
    },
    {
        "id": "c19", "name": "Arizona Tile", "company": "Arizona Tile (Morena Blvd)", "role": "Tile supplier",
        "email": "", "phone": "(619) 276-3915", "category": "vendor", "nextAction": "", "nextDue": "",
        "notes": "2–3 day availability. Used on Ken's C St. tile project.", "log": [],
    },
    {
        "id": "c20", "name": "Urban Surfaces", "company": "Urban Surfaces", "role": "Vendor (LVP/resilient)",
        "email": "", "phone": "", "category": "vendor",
        "nextAction": "Product knowledge session Tue 6/16, 8:30am (after AM meeting)", "nextDue": "2026-06-16",
        "notes": "Combined PK session on calendar — get rep name and contact there.", "log": [],
    },
    {
        "id": "c21", "name": "Joe", "company": "Boys to Men Mentoring", "role": "Executive / event organizer",
        "email": "joe@boystomen.org", "phone": "619-889-9243", "category": "bd",
        "nextAction": "Coffee at Sheldon's La Mesa, 8401 La Mesa Blvd — Tue 6/16 8:30am", "nextDue": "2026-06-16",
        "notes": "Corky intro'd Bob to the Boys to Men 100 Wave Challenge — premier SD surf charity event. Joe wants Bob involved. Bob responded warmly re: surfing his whole life and sharing it. Real community connection, not just BD. Coffee confirmed for Tuesday.",
        "log": [
            {"date": "2026-06-13", "note": "Corky intro'd via email. Joe reached out about 100 Wave Challenge."},
            {"date": "2026-06-14", "note": "Bob accepted coffee invite — Sheldon's La Mesa, Tue 6/16 8:30am."},
        ],
    },
    {
        "id": "c22", "name": "Fordyce Construction", "company": "Fordyce Construction (Bid Desk)",
        "role": "GC (bid invite)", "email": "BidInvitation@fordyceconstruction.com", "phone": "", "category": "gc",
        "nextAction": "Review bid invite: 405 E Lexington Ave TI — decide pursue or pass", "nextDue": "2026-06-17",
        "notes": "Bid invite received 6/15 for 405 E Lexington Avenue Tenant Improvements. New GC — first contact. Forwarded by Olga.",
        "log": [{"date": "2026-06-15", "note": "Bid invite received for 405 E Lexington Ave TI."}],
    },
    {
        "id": "c23", "name": "PE Dept / iCON National", "company": "iCON National", "role": "Project Engineering",
        "email": "PE@iconnational.com", "phone": "", "category": "gc",
        "nextAction": "Sign Baypointe SCO#8 DocuSign — Unit 124 & Wainscot Tile", "nextDue": "2026-06-16",
        "notes": "DocuSign reminder received 6/15 for Baypointe SCO#8. Needs signature.",
        "log": [{"date": "2026-06-15", "note": "DocuSign reminder: Baypointe Star Flooring SCO#8 Unit 124 & Wainscot Tile."}],
    },
    {
        "id": "c24", "name": "Praecelsus PM / 626 Brightwood", "company": "Praecelsus", "role": "Property manager",
        "email": "", "phone": "", "category": "gc",
        "nextAction": "Respond to Ken & Rudy — customer texting repeatedly, needs resolution on labor-only revised estimate",
        "nextDue": "2026-06-15",
        "notes": "Hot item. Ken circled back 6/15, customer sent multiple texts Friday + Monday. Amanda escalated. Revised labor-only estimate attached. Needs leadership decision to proceed.",
        "log": [{"date": "2026-06-15", "note": "Ken + Rudy both flagging. Customer texting repeatedly. Amanda escalated to Bob."}],
    },
    {
        "id": "c25", "name": "Aly Ibrahem", "company": "Zuma Restaurant", "role": "Owner / client contact",
        "email": "a.ibrahem@zumarestaurant.com", "phone": "", "category": "gc", "nextAction": "", "nextDue": "",
        "notes": "Contact shared by Luis 6/15. Active Zuma floor leveling job. Luis handling direct communications.",
        "log": [{"date": "2026-06-15", "note": "Luis shared email contact for Zuma job."}],
    },
    {
        "id": "c26", "name": "Pete Hunter", "company": "Highland Construction", "role": "Project Executive",
        "email": "Pete.Hunter@highland-ca.com", "phone": "(619) 871-3977", "category": "gc",
        "nextAction": "Schedule site visit — 3949 Ohio St. project", "nextDue": "2026-06-18",
        "notes": "Intro'd by Joaquin Hernandez (Caster/Serving Hands) 5/22. Bob already reviewed drawings and offered to build resilient flooring proposal. Site visit needed.",
        "log": [{"date": "2026-05-22", "note": "E-intro from Joaquin. Bob responded — reviewing drawings, will call to touch base."}],
    },
]
