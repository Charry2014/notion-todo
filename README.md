# Notion TO-DO Finder

Notion is my primary organiser and documentation tool for my work - I have a single database for all work related documents such as meeting minutes, reminders, to-do items, meeting preparation notes and so on. In the database there is a 'type' field where the entries are categorised. Typically I will be sitting in a meeting, with meeting minutes open, will hear some item that I need to remember to work on and rather than break out into a new Notion page I just make a TODO note in the meeting minutes. But all too often these to-do items got forgotten.

This helper script extracts TODO notes in database pages and turns them into their own to-do database entries. It runs locally on your computer and accesses your cloud-hosted Notion space. One suggested way to run this script is to use a cron job after the end of the work day to extract all the TODO notes made during the day.

The script scans a Notion database to find all entries created on a specific day. It scans all lines of these pages and finds lines flagged with TODO labels, and create database entries for these elements. Once a database entry has been created the TODO is changed to a DONE in the source page.

The script works with the new Notion API as of September 2025, which has broken a lot of the docs on the internet.

This script is developed and tested on Windows 11 but should work fine on everything else.

## Usage

main.py [--date dd.mm.yyyy]

If no date argument is given it defaults to today.

## Script Modifications

You may need to adjust the following constants in the script to match your own database -

 * TITLE_PROP = "Title"        # The name of your database's Title property
 * TYPE_PROP = "Type"         # A 'Select' property for the item type
 * TAGS_PROP = "Tags"         # A 'Multi-select' property for tags

The TYPE_PROP will be set to 'To-Do' when a new item is created. 
The TAGS_PROP will be set to 'Auto Generated' in the new item so new entries can be found easily for review.

Finally, it is quite likely that other adjustments will be needed to your particular database structure and workflow.

## Setup

There are a few steps needed. Notion recently (Sept 2025) changed the way things work and the internet and all AI agents haven't kept up which doesn't help. 

As of November 2025 this is what works -

 * Create a Notion integration in your workspace and get a token for this
 * Allow this integration to access your database
 * Find the database ID so your integration can access the data
 * Run the script that runs locally and uses the integration to access the database

### Create a Notion Integration and Get The Token


    1. Go to https://www.notion.so/my-integrations (or open Settings → Connections → Develop and Manage Integrations).
    2. Create a new integration (Internal Integration).
    3. Copy the integration's "Internal Integration Token" (a secret string that is a UUID) and set it as NOTION_TOKEN environment variable:
        - Linux/macOS: export NOTION_TOKEN="ABCDEF_xxx"
        - Windows (PowerShell): $env:NOTION_TOKEN = "ABCDEF_xxx"
    4. Allow the integration to read and update existing content as well as create new content

### Grant Access to the Database

This has changed recently and all instruction on the internet are currently wrong. Your new integration needs permission to access to your database.

To do this -

    * Open the database in Notion, press … → Connections → find your integration, and give it access.

### Find the Database ID

This one I never mastered but it became a trial-and-error game. If you have created a new database and have only one view on it then it all seems to be easy but for larger spaces with multiple views on the same database it seems you have to find 'the one', the first view made. Anyway, you need to find the correct ID, the wrong ones will return no data.

- NOTION_DATABASE_ID:
    1. Open the database page in Notion.
    2. The URL will be something like https://www.notion.so/abababfa9deffbadede578abcde?v=abcdef12340ffbabcdef1234fabc where the long string after the last dash in the notion.so address and before the ?v= is the database id. It does not have hyphens in the current version.
    3. Set NOTION_DATABASE_ID env var:
        - export NOTION_DATABASE_ID="27834a68fa998088a103c036ff95bc03"
