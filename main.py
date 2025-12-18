#!/usr/bin/env python3
# notion_todo_importer.py (v5 - marks original TODO as DONE)

import os
import argparse
import re
from datetime import datetime, timezone, timedelta
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
SUB_ITEM_PROP = "Sub-item"  # The name of your relation property for sub-items
PARENT_ITEM_PROP = "Parent item" # The name of your relation property for parent item

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

# Regex to find "TODO" and its synonyms at the beginning of a line (after spaces/punctuation), case-insensitive
TODO_DETECT = re.compile(r"^[\s\W]*(todo|to-?do|to\s+do)\b", re.IGNORECASE)
# Regex to remove "TODO" keyword and following colons/spaces ONLY from the beginning of a line
TODO_REMOVE = re.compile(r"^[\s\W]*(todo|to-?do|to\s+do)[\s:]*", re.IGNORECASE)

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
        # Replace only the first occurrence of a TODO pattern with DONE
        modified_content = TODO_REMOVE.sub("DONE ", original_content, count=1)
        
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

def create_todo_page(source_page: dict, todo_text: str, following_list_blocks: list = None) -> bool:
    """Creates a new page in the database for a found TODO item.
    	returns True on success, False on failure.
	"""
    if following_list_blocks is None:
        following_list_blocks = []

    source_page_id = source_page["id"]
    source_page_title = get_page_title(source_page)
    source_page_url = source_page.get("url", f"https://www.notion.so/{source_page_id.replace('-', '')}")

    # Remove the TODO keyword from the text for the page content
    # First strip leading/trailing whitespace, then remove TODO pattern
    clean_text = todo_text.strip()
    # Remove TODO and everything before it (like leading punctuation), keep everything after
    clean_text = TODO_REMOVE.sub("", clean_text).strip()
    # Remove any remaining leading punctuation (colon, hyphen, etc.) and whitespace
    clean_text = re.sub(r"^[:\-\s]+", "", clean_text).strip()

    new_page_title = f"{clean_text}"

    print(f"  - Found TODO: '{clean_text}'")

    # Duplicate check is currently disabled to avoid false negatives.
    #if check_for_duplicate_todo(todo_text, source_page_id):
    #    print("    -> Skipping, duplicate already exists.")
    #    return

    # Get the parent item from the source page (if it exists)
    source_properties = source_page.get("properties", {})
    parent_relation = source_properties.get(PARENT_ITEM_PROP, {}).get("relation", [])

    # Build properties for the new page
    new_page_properties = {
        TITLE_PROP: {"title": [{"text": {"content": new_page_title}}]},
        TYPE_PROP: {"select": {"name": "To-Do"}},
        TAGS_PROP: {"multi_select": [{"name": "Auto Generated"}]},
        PARENT_ITEM_PROP: {"relation": [{"id": source_page_id}]}  # Set source page as parent
    }

    # If source page has a parent, inherit it
    if parent_relation:
        new_page_properties[PARENT_ITEM_PROP] = {"relation": parent_relation}

    # Build the children blocks for the new page
    children_blocks = [
        {
            "object": "block",
            "type": "paragraph",
            "paragraph": {
                "rich_text": [{"type": "text", "text": {"content": clean_text}}]
            },
        },
    ]

    # Add any following list items from the source page
    for list_block in following_list_blocks:
        block_type = list_block.get("type")
        if block_type in ("bulleted_list_item", "numbered_list_item"):
            # Copy the rich_text content from the original block
            original_rich_text = list_block.get(block_type, {}).get("rich_text", [])
            children_blocks.append({
                "object": "block",
                "type": block_type,
                block_type: {
                    "rich_text": original_rich_text
                }
            })

    # Add the source link at the end
    children_blocks.append({
        "object": "block",
        "type": "paragraph",
        "paragraph": {
            "rich_text": [
                {"type": "text", "text": {"content": "Source: "}},
                {"type": "text", "text": {"content": "Link to original page", "link": {"url": source_page_url}}},
            ]
        },
    })

    if following_list_blocks:
        print(f"    -> Including {len(following_list_blocks)} list item(s)")

    try:
        new_page = client.pages.create(
            parent={"database_id": NOTION_DATABASE_ID},
            properties=new_page_properties,
            children=children_blocks,
        )
        print("    -> Created new To-Do page.")

        # Update the source page to add this new page to its Sub-item relation
        new_page_id = new_page["id"]
        try:
            # Get current sub-items from source page
            current_sub_items = source_properties.get(SUB_ITEM_PROP, {}).get("relation", [])
            # Add the new page to the sub-items list
            updated_sub_items = current_sub_items + [{"id": new_page_id}]

            client.pages.update(
                page_id=source_page_id,
                properties={
                    SUB_ITEM_PROP: {"relation": updated_sub_items}
                }
            )
            print("    -> Added to source page as sub-item.")
        except APIResponseError as e:
            print(f"    -> Warning: Could not update source page sub-items. Error: {e}")

        return True # Return True on success
    except APIResponseError as e:
        print(f"    -> Failed to create page. Error: {e}")
        return False # Return False on failure

def process_date(target_date):
    """Process TODOs for a specific date."""
    print(f"Scanning Notion database for pages created on: {target_date.strftime('%d.%m.%Y')}")

    target_iso_date = target_date.isoformat()
    # Use server side filtering - exclude auto-generated pages to prevent infinite loops
    date_filter = {
        "and": [
            {
                "or": [
                    # Condition 1: The built-in 'Created time' property matches the date.
                    {"property": "Created", "created_time": {"equals": target_iso_date}},
                    # Condition 2: The custom 'Finish Before' date property matches the date.
                    {"property": FINISH_BEFORE_PROP, "date": {"equals": target_iso_date}}
                ]
            },
            # Exclude pages tagged as "Auto Generated"
            {"property": TAGS_PROP, "multi_select": {"does_not_contain": "Auto Generated"}}
        ]
    }

    for page in get_all_database_pages(NOTION_DATABASE_ID, date_filter):
        page_title = get_page_title(page)
        print(f"Scanning page: '{page_title}'")

        blocks = get_page_blocks(page["id"])
        found_todos = False

        # List block types that should be collected as sub-items
        list_block_types = {"bulleted_list_item", "numbered_list_item"}

        i = 0
        while i < len(blocks):
            block = blocks[i]
            text_content = extract_text_from_block(block)
            for line in text_content.splitlines():
                if TODO_DETECT.search(line):
                    found_todos = True
                    # Clean up the line by removing checkbox syntax and extra whitespace
                    clean_line = re.sub(r"^\s*\[\s*[xX]?\s*\]\s*", "", line).strip()

                    # Collect following list items
                    following_list_blocks = []
                    j = i + 1
                    while j < len(blocks) and blocks[j].get("type") in list_block_types:
                        following_list_blocks.append(blocks[j])
                        j += 1

                    # Step 1: Try to create the new To-Do page with list items
                    is_successful = create_todo_page(page, clean_line, following_list_blocks)

                    # Step 2: If successful, update the original block
                    if is_successful:
                        mark_todo_as_done(block)
                    break  # Only process the first TODO line per block
            i += 1

        if not found_todos:
            print("  - No TODOs found on this page.")

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
    parser.add_argument(
        "--since",
        default=None,
        help="Process all dates from this date (dd.mm.yyyy format) to today."
    )
    args = parser.parse_args()

    # Check for mutually exclusive options
    if args.date and args.since:
        raise SystemExit("Error: Cannot use both --date and --since options together.")

    if args.since:
        # Process all dates from --since to today
        start_date = parse_date_input(args.since)
        end_date = datetime.now(timezone.utc).date()

        if start_date > end_date:
            raise SystemExit(f"Error: Start date {start_date.strftime('%d.%m.%Y')} is in the future.")

        current_date = start_date
        print(f"Processing dates from {start_date.strftime('%d.%m.%Y')} to {end_date.strftime('%d.%m.%Y')}")
        print("=" * 80)

        while current_date <= end_date:
            process_date(current_date)
            print("-" * 80)
            current_date += timedelta(days=1)

        print(f"\nCompleted processing {(end_date - start_date).days + 1} days.")
    else:
        # Process single date (original behavior)
        target_date = parse_date_input(args.date)
        process_date(target_date)

if __name__ == "__main__":
    main()