#!/usr/bin/env python3
# notion_todo_importer.py (v3 - with pagination fix)

import os
import argparse
import re
from datetime import datetime, timezone
from notion_client import Client
from notion_client.errors import APIResponseError

# --- Configuration ---
# Change these strings to match the property names in your Notion database.
TITLE_PROP = "Name"        # The name of your database's Title property
TYPE_PROP = "Type"         # A 'Select' property for the item type
TAGS_PROP = "Tags"         # A 'Multi-select' property for tags

# --- Environment Variable Setup ---
NOTION_TOKEN = os.environ.get("NOTION_TOKEN")
NOTION_DATABASE_ID = os.environ.get("NOTION_DATABASE_ID")

if not NOTION_TOKEN or not NOTION_DATABASE_ID:
    raise SystemExit(
        "Error: Please set NOTION_TOKEN and NOTION_DATABASE_ID environment variables."
    )

# Initialize the Notion Client
try:
    client = Client(auth=NOTION_TOKEN)
except Exception as e:
    raise SystemExit(f"Error initializing Notion client: {e}")

# Regex to find "TODO" and its synonyms, case-insensitive
TODO_PATTERNS = re.compile(r"\b(todo|to-?do|to do|todo:|to-do:)\b", re.IGNORECASE)

def parse_date_input(date_str: str | None) -> datetime.date:
    """Parses a 'dd.mm.yyyy' string into a date object. Defaults to today's date (UTC) if None."""
    if not date_str:
        return datetime.now(timezone.utc).date()
    try:
        return datetime.strptime(date_str, "%d.%m.%Y").date()
    except ValueError:
        raise SystemExit(f"Invalid date format: '{date_str}'. Please use dd.mm.yyyy.")

def get_all_database_pages(database_id: str):
    """Generator to yield all pages from a database, handling pagination correctly."""
    next_cursor = None
    while True:
        try:
            # Build query parameters, only including start_cursor if it exists
            query_params = {"database_id": database_id}
            if next_cursor:
                query_params["start_cursor"] = next_cursor

            response = client.databases.query(**query_params)

            yield from response.get("results", [])
            next_cursor = response.get("next_cursor")
            if not response.get("has_more"):
                break
        except APIResponseError as e:
            print(f"Error querying database: {e}")
            break

def get_page_blocks(page_id: str):
    """Fetches all top-level blocks from a page, handling pagination correctly."""
    all_blocks = []
    next_cursor = None
    while True:
        try:
            # Build query parameters, only including start_cursor if it exists
            query_params = {"block_id": page_id}
            if next_cursor:
                query_params["start_cursor"] = next_cursor

            response = client.blocks.children.list(**query_params)
            
            all_blocks.extend(response.get("results", []))
            next_cursor = response.get("next_cursor")
            if not response.get("has_more"):
                break
        except APIResponseError as e:
            print(f"Error fetching blocks for page {page_id}: {e}")
            break
    return all_blocks

def extract_text_from_block(block: dict) -> str:
    """Extracts plain text from a Notion block, if available."""
    block_type = block.get("type")
    if block_type in block and "rich_text" in block[block_type]:
        return "".join(rt.get("plain_text", "") for rt in block[block_type]["rich_text"])
    return ""

def get_page_title(page: dict) -> str:
    """Extracts the plain text title from a page object."""
    properties = page.get("properties", {})
    if TITLE_PROP in properties and properties[TITLE_PROP]["type"] == "title":
        return "".join(t.get("plain_text", "") for t in properties[TITLE_PROP]["title"])
    return "Untitled"

def check_for_duplicate_todo(todo_text: str, source_page_id: str):
    """Checks if an auto-generated TODO for this source page and text already exists."""
    try:
        # A simple filter to narrow down the search space
        response = client.databases.query(
            database_id=NOTION_DATABASE_ID,
            filter={
                "and": [
                    {"property": TAGS_PROP, "multi_select": {"contains": "Auto Generated"}},
                    {"property": TITLE_PROP, "title": {"contains": "TODO"}}
                ]
            }
        )
        
        # Now, more accurately check the results
        for page in response.get("results", []):
            page_blocks = get_page_blocks(page["id"])
            # Check if the body of the TODO page contains the same text
            body_text_match = any(todo_text in extract_text_from_block(b) for b in page_blocks)
            # Check if it links back to the same source page
            source_link_match = False
            for block in page_blocks:
                if "paragraph" in block:
                    for rt in block["paragraph"]["rich_text"]:
                        link_url = rt.get("text", {}).get("link", {}).get("url", "")
                        if source_page_id.replace("-", "") in link_url:
                            source_link_match = True
                            break
                if source_link_match:
                    break
            
            if body_text_match and source_link_match:
                return True # Confirmed duplicate
        return False
    except APIResponseError as e:
        print(f"Warning: Could not check for duplicates due to API error: {e}")
        return False

def create_todo_page(source_page: dict, todo_text: str, counter: int):
    """Creates a new page in the database for a found TODO item."""
    source_page_id = source_page["id"]
    source_page_title = get_page_title(source_page)
    source_page_url = source_page.get("url", f"https://www.notion.so/{source_page_id.replace('-', '')}")

    new_page_title = f"TODO {source_page_title} {counter:02d}"

    print(f"  - Found TODO: '{todo_text}'")

    if check_for_duplicate_todo(todo_text, source_page_id):
        print("    -> Skipping, duplicate already exists.")
        return

    try:
        client.pages.create(
            parent={"database_id": NOTION_DATABASE_ID},
            properties={
                TITLE_PROP: {"title": [{"text": {"content": new_page_title}}]},
                TYPE_PROP: {"select": {"name": "To-Do"}},
                TAGS_PROP: {"multi_select": [{"name": "Auto Generated"}]},
            },
            children=[
                {
                    "object": "block", "type": "paragraph",
                    "paragraph": {"rich_text": [{"type": "text", "text": {"content": todo_text}}]},
                },
                {
                    "object": "block", "type": "paragraph",
                    "paragraph": {
                        "rich_text": [
                            {"type": "text", "text": {"content": "Source: "}},
                            {"type": "text", "text": {"content": "Link to original page", "link": {"url": source_page_url}}},
                        ]
                    },
                },
            ],
        )
        print("    -> Created new To-Do page.")
    except APIResponseError as e:
        print(f"    -> Failed to create page. Error: {e}")

def main():
    parser = argparse.ArgumentParser(
        description="Scan a Notion database for pages created on a specific date and extract TODOs."
    )
    parser.add_argument(
        "date", nargs="?", default=None,
        help="Date in dd.mm.yyyy format. If omitted, defaults to today.",
    )
    args = parser.parse_args()

    target_date = parse_date_input(args.date)
    print(f"Scanning Notion database for pages created on: {target_date.strftime('%d.%m.%Y')}")

    pages_on_date = []
    for page in get_all_database_pages(NOTION_DATABASE_ID):
        created_time_str = page.get("created_time")
        if created_time_str:
            page_date = datetime.fromisoformat(created_time_str.replace("Z", "+00:00")).date()
            if page_date == target_date:
                pages_on_date.append(page)

    if not pages_on_date:
        print("No pages found for the specified date.")
        return

    print(f"Found {len(pages_on_date)} page(s) to scan...")

    for page in pages_on_date:
        page_title = get_page_title(page)
        print(f"Scanning page: '{page_title}'")
        
        blocks = get_page_blocks(page["id"])
        todo_counter = 1
        found_todos = False

        for block in blocks:
            text_content = extract_text_from_block(block)
            for line in text_content.splitlines():
                if TODO_PATTERNS.search(line):
                    found_todos = True
                    clean_line = re.sub(r"^\s*\[\s*[xX]?\s*\]\s*", "", line).strip()
                    create_todo_page(page, clean_line, todo_counter)
                    todo_counter += 1
        
        if not found_todos:
            print("  - No TODOs found on this page.")

if __name__ == "__main__":
    main()