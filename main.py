#!/usr/bin/env python3
# notion_todo_importer.py (v5 - marks original TODO as DONE)

import os
import argparse
import re
from datetime import datetime, timezone
from notion_client import Client
from notion_client.errors import APIResponseError

# --- Configuration ---
# Change these strings to match the property names in your Notion database.
TITLE_PROP = "Title"        # The name of your database's Title property
TYPE_PROP = "Type"         # A 'Select' property for the item type
TAGS_PROP = "Tags"         # A 'Multi-select' property for tags
# Pages created on the given date will be processed, and any marked as finished
# on that date also. This property name defines those finished pages.
FINISH_BEFORE_PROP = "Finish Before" # The name of your custom date property

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

def get_all_database_pages(database_id: str, filter: str):
    """Generator to yield all pages from a database, handling pagination."""
    next_cursor = None
    db = client.databases.retrieve(database_id=database_id)
    print(f"Getting data from {db['data_sources'][0]}")
    while True:
        try:
            response = client.data_sources.query(data_source_id=db['data_sources'][0]['id'], filter=filter, next_cursor=next_cursor)
            yield from response.get("results", [])
            next_cursor = response.get("next_cursor")
            if not response.get("has_more"):
                break
        except APIResponseError as e:
            print(f"Error querying database: {e}")
            break

def get_page_blocks(page_id: str):
    """Fetches all top-level blocks from a page, handling pagination."""
    all_blocks = []
    next_cursor = None
    while True:
        try:
            query_params = {"block_id": page_id, "page_size": 100}
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
    retval = "Untitled"
    try:
       retval = properties[TITLE_PROP]["title"][0]["text"]["content"]
    except:
        pass # There is apparently no title for this page 
    return retval

def mark_todo_as_done(block: dict):
    """Updates a block to replace 'TODO' with 'DONE' and checks the box if applicable."""
    block_id = block["id"]
    block_type = block["type"]
    original_rich_text = block[block_type].get("rich_text", [])

    # Create a new rich_text array with the keyword replaced
    new_rich_text = []
    for text_obj in original_rich_text:
        original_content = text_obj.get("text", {}).get("content", "")
        # Replace only the first occurrence of a TODO pattern in the text segment
        modified_content = TODO_PATTERNS.sub("DONE", original_content, count=1)
        
        # Create a new text object; do not modify the original in place
        new_text_obj = text_obj.copy()
        new_text_obj["text"]["content"] = modified_content
        new_rich_text.append(new_text_obj)

    # Construct the payload for the update API call
    update_payload = {
        block_type: {
            "rich_text": new_rich_text
        }
    }

    # If it's a to_do block, also mark it as checked
    if block_type == "to_do":
        update_payload[block_type]["checked"] = True

    try:
        client.blocks.update(block_id=block_id, **update_payload)
        print("    -> Marked original item as DONE.")
        return True
    except APIResponseError as e:
        print(f"    -> Failed to mark original as DONE. Error: {e}")
        return False

def check_for_duplicate_todo(todo_text: str, source_page_id: str):
    """
    Checks if an auto-generated TODO for this source page and text already exists.
    This is a heuristic to prevent creating the same item multiple times.
    Unused in the current script, but I chose to leave it here for reference.
    """
    try:
        db = client.databases.retrieve(database_id=NOTION_DATABASE_ID)
        filter={
            "and": [
                {"property": TAGS_PROP, "multi_select": {"contains": "Auto Generated"}},
                {"property": TITLE_PROP, "title": {"contains": todo_text[:50]}} # Check against a substring
            ]
        }
        response = client.data_sources.query(data_source_id=db['data_sources'][0]['id'], filter=filter)

        # Further check if any result links back to the same source page
        for page in response.get("results", []):
            page_content = get_page_blocks(page["id"])
            for block in page_content:
                if "paragraph" in block:
                    for rt in block["paragraph"]["rich_text"]:
                        if rt.get("text", {}).get("link", {}).get("url", "").endswith(source_page_id.replace("-", "")):
                            return True
        return False
    except APIResponseError as e:
        print(f"Warning: Could not check for duplicates due to API error: {e}")
        return False # Fail open to allow creation

def create_todo_page(source_page: dict, todo_text: str, counter: int) -> bool:
    """Creates a new page in the database for a found TODO item.
    	returns True on success, False on failure.
	"""
    source_page_id = source_page["id"]
    source_page_title = get_page_title(source_page)
    source_page_url = source_page.get("url", f"https://www.notion.so/{source_page_id.replace('-', '')}")

    new_page_title = f"TODO {source_page_title} {counter:02d}"

    print(f"  - Found TODO: '{todo_text}'")

    #if check_for_duplicate_todo(todo_text, source_page_id):
    #    print("    -> Skipping, duplicate already exists.")
    #    return

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
                    "object": "block",
                    "type": "paragraph",
                    "paragraph": {
                        "rich_text": [{"type": "text", "text": {"content": todo_text}}]
                    },
                },
                {
                    "object": "block",
                    "type": "paragraph",
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
        return True # Return True on success
    except APIResponseError as e:
        print(f"    -> Failed to create page. Error: {e}")
        return False # Return False on failure

def main():
    parser = argparse.ArgumentParser(
        description="Scan a Notion database for pages created on a specific date and extract TODOs."
    )
    parser.add_argument(
        "--date",
        nargs="?",
        default=None,
        help="Date in dd.mm.yyyy format. If omitted, defaults to today."
    )
    args = parser.parse_args()

    target_date = parse_date_input(args.date)
    print(f"Scanning Notion database for pages created on: {target_date.strftime('%d.%m.%Y')}")

    target_iso_date = target_date.isoformat()
    # Use server side filtering
    date_filter = {
        "or": [
            # Condition 1: The built-in 'Created time' property matches the date.
            {"property": "Created", "created_time": {"equals": target_iso_date}},
            # Condition 2: The custom 'Finish Before' date property matches the date.
            {"property": FINISH_BEFORE_PROP, "date": {"equals": target_iso_date}}
        ]
    }

    for page in get_all_database_pages(NOTION_DATABASE_ID, date_filter):
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
                    # Clean up the line by removing checkbox syntax and extra whitespace
                    clean_line = re.sub(r"^\s*\[\s*[xX]?\s*\]\s*", "", line).strip()
	                # Step 1: Try to create the new To-Do page
                    is_successful = create_todo_page(page, clean_line, todo_counter)
                
        	        # Step 2: If successful, update the original block
                    if is_successful:
                        mark_todo_as_done(block)
                        todo_counter += 1
        
        if not found_todos:
            print("  - No TODOs found on this page.")

if __name__ == "__main__":
    main()