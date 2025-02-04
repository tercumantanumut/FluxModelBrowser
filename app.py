import json
import time
import os
from hashlib import sha256
from flask import Flask, jsonify, request
from huggingface_hub import HfApi, login
from apscheduler.schedulers.background import BackgroundScheduler
import logging
from flask_cors import CORS
from dotenv import load_dotenv
import shutil
from datetime import datetime, timezone
import requests

# Set up logging
logging.basicConfig(level=logging.DEBUG, format='%(asctime)s - %(levelname)s - %(message)s')

# Load environment variables from .env file
load_dotenv(dotenv_path=os.path.join(os.path.dirname(__file__), '..', '.env'))

app = Flask(__name__)
CORS(app)

LORA_MODELS_FILE = 'lora_models.json'
HF_TOKEN = os.getenv('HUGGINGFACE_TOKEN')

# Login to Hugging Face Hub
if HF_TOKEN:
    try:
        login(token=HF_TOKEN)
        logging.info("Logged in to Hugging Face Hub")
    except Exception as e:
        logging.error(f"Failed to log in to Hugging Face Hub: {str(e)}")
else:
    logging.warning("HUGGINGFACE_TOKEN not found in .env file. Running without authentication.")

api = HfApi(token=HF_TOKEN)

def create_model_id(model):
    unique_string = f"https://huggingface.co/{model.id}_{getattr(model, 'createdAt', '')}"
    return sha256(unique_string.encode()).hexdigest()

def format_date(date):
    if date is None:
        return None
    if isinstance(date, str):
        try:
            # Replace 'Z' with '+00:00' to properly parse UTC dates
            date = datetime.fromisoformat(date.replace('Z', '+00:00'))
        except ValueError:
            logging.warning(f"Invalid date string: {date}")
            return None
    if not isinstance(date, datetime):
        logging.warning(f"Invalid date type: {type(date)}")
        return None
    # Example: if the date matches a known default value from HF models, return a custom string.
    if date == datetime(2022, 3, 2, 23, 29, 4, tzinfo=timezone.utc):
        return "Before March 2022"
    return date.strftime("%Y-%m-%d")

def load_json_file(filepath):
    try:
        with open(filepath, 'r', encoding='utf-8') as f:  # Ensure UTF-8 encoding is used when reading the file
            return json.load(f)
    except json.JSONDecodeError as e:
        logging.error(f"Error decoding {filepath}: {str(e)}")
        return []
    except Exception as e:
        logging.error(f"Unexpected error when loading {filepath}: {str(e)}")
        return []

def fetch_models_from_huggingface():
    processed_models = []
    flux_models = []
    other_models = []
    total_models = 0

    logging.info("Starting to fetch LoRA models from Hugging Face")

    model_list = list(api.list_models(filter="lora", search="flux"))
    model_count = len(model_list)

    for model in model_list:
        total_models += 1
        try:
            created_at = getattr(model, 'createdAt', None)
            formatted_date = format_date(created_at)
            
            if formatted_date is None:
                logging.warning(f"Could not format date for model {model.id}. createdAt: {created_at}")

            model_data = {
                'id': create_model_id(model),
                'name': model.id,
                'downloads': getattr(model, 'downloads', 0),
                'likes': getattr(model, 'likes', 0),
                'date': formatted_date,
                'link': f"https://huggingface.co/{model.id}",
                'image_url': model.cardData.get('thumbnail') if hasattr(model, 'cardData') and model.cardData else ""
            }

            logging.debug(f"Processed model {model.id}: createdAt={created_at}, formatted_date={formatted_date}")

            if "flux" in model.id.lower():
                flux_models.append(model_data)
            else:
                other_models.append(model_data)
        except Exception as e:
            logging.error(f"Error processing model {model.id}: {str(e)}")
            continue

        if total_models % 100 == 0:
            logging.info(f"Processed {total_models} models so far")

    flux_models.sort(key=lambda x: x['likes'], reverse=True)
    other_models.sort(key=lambda x: x['likes'], reverse=True)

    processed_models = flux_models + other_models

    logging.info(f"Total Hugging Face models fetched: {model_count}")
    logging.info(f"Flux models found: {len(flux_models)}")
    logging.info(f"Other models included: {len(other_models)}")
    logging.info(f"Total processed models: {len(processed_models)}")

    return processed_models

def fetch_models_from_civitai(base_models):
    url = "https://civitai.com/api/v1/models"
    params = {
        'baseModels': base_models,
        'limit': 100  # Fetch maximum allowed models per request
    }
    models = []
    next_cursor = None
    page = 1

    while True:
        if next_cursor:
            params['cursor'] = next_cursor
        
        response = requests.get(url, params=params)
        
        if response.status_code != 200:
            logging.error(f"Failed to fetch data from Civitai: {response.status_code}")
            break
        
        data = response.json()
        new_items = data.get('items', [])
        models.extend(new_items)

        logging.info(f"Fetched page {page} with {len(new_items)} models. Total models so far: {len(models)}")

        # Check for the next cursor
        next_cursor = data.get('metadata', {}).get('nextCursor')
        if not next_cursor:
            logging.info("No more pages to fetch.")
            break  # No more pages to fetch

        page += 1

    logging.info(f"Finished fetching from Civitai. Total models: {len(models)}")
    return models

def extract_model_details_from_civitai(model_data):
    models = []
    for item in model_data:
        model_id = item.get('id')
        model_name = item.get('name')
        downloads = item.get('stats', {}).get('downloadCount', 0)
        likes = item.get('stats', {}).get('thumbsUpCount', 0)
        
        # Try to grab the date from the top-level fields.
        date_published = item.get('publishedAt') or item.get('updatedAt')
        # If not available, check the first model version.
        if not date_published and item.get('modelVersions'):
            first_version = item.get('modelVersions')[0]
            date_published = first_version.get('publishedAt') or first_version.get('updatedAt')
        formatted_date = format_date(date_published)
        
        link = f"https://civitai.com/models/{model_id}"
        image_url = ""

        # Iterate through all model versions to find an image
        for model_version in item.get('modelVersions', []):
            images = model_version.get('images', [])
            if images:
                image_url = images[0].get('url', "")
                break  # Use the first image found

        models.append({
            'id': model_id,
            'name': model_name,
            'downloads': downloads,
            'likes': likes,
            'date': formatted_date,
            'link': link,
            'image_url': image_url
        })
    return models

def fetch_and_save_lora_models():
    try:
        huggingface_models = fetch_models_from_huggingface()

        base_models = ['Flux.1 D', 'Flux.1 S']  # Adjust base models as needed
        civitai_model_data = fetch_models_from_civitai(base_models)
        civitai_models = extract_model_details_from_civitai(civitai_model_data) if civitai_model_data else []

        all_models = huggingface_models + civitai_models

        logging.info(f"Total models from both sources: {len(all_models)}")

        backup_file = f"{LORA_MODELS_FILE}.bak"
        if os.path.exists(LORA_MODELS_FILE):
            shutil.copy(LORA_MODELS_FILE, backup_file)
            logging.info(f"Backup of the existing file created: {backup_file}")

        # Ensure the file is written with UTF-8 encoding
        with open(LORA_MODELS_FILE, 'w', encoding='utf-8') as f:
            json.dump(all_models, f, ensure_ascii=False, indent=4)
            f.flush()
            os.fsync(f.fileno())

        logging.info(f"LoRA models updated: {len(all_models)} models saved to {LORA_MODELS_FILE}")
    except Exception as e:
        logging.error(f"Error fetching models: {str(e)}")
        raise

def ensure_lora_models_file():
    if not os.path.exists(LORA_MODELS_FILE):
        logging.info(f"{LORA_MODELS_FILE} not found. Fetching models...")
        try:
            fetch_and_save_lora_models()
        except Exception as e:
            logging.error(f"Error ensuring lora_models.json file: {str(e)}")
            with open(LORA_MODELS_FILE, 'w') as f:
                json.dump([], f)
    else:
        logging.info(f"{LORA_MODELS_FILE} already exists")

@app.route('/api/lora-models', methods=['GET'])
def get_lora_models():
    try:
        page = int(request.args.get('page', 1))
        limit = int(request.args.get('limit', 20))
        search_term = request.args.get('search', '').lower()
        sort_by = request.args.get('sortBy', 'likes')
        
        if not os.path.exists(LORA_MODELS_FILE):
            logging.error(f"{LORA_MODELS_FILE} not found. Fetching models...")
            fetch_and_save_lora_models()

        models = load_json_file(LORA_MODELS_FILE)

        if not models:
            logging.warning("No models found in the file. Attempting to fetch again...")
            fetch_and_save_lora_models()
            models = load_json_file(LORA_MODELS_FILE)

        # Filter models based on search term
        filtered_models = [model for model in models if search_term in model['name'].lower()]

        # Sort models
        if sort_by in ['likes', 'downloads']:
            filtered_models.sort(key=lambda x: x[sort_by], reverse=True)
        elif sort_by == 'date':
            filtered_models.sort(key=lambda x: x['date'] or "", reverse=True)
        elif sort_by == 'name':
            filtered_models.sort(key=lambda x: x['name'].lower())

        # Paginate results
        start_index = (page - 1) * limit
        end_index = start_index + limit
        paginated_models = filtered_models[start_index:end_index]

        has_more = end_index < len(filtered_models)

        logging.info(f"Returning {len(paginated_models)} models for page {page}")
        return jsonify({
            "models": paginated_models,
            "hasMore": has_more,
            "totalCount": len(filtered_models)
        })
    except json.JSONDecodeError as e:
        logging.error(f"Error decoding {LORA_MODELS_FILE}: {str(e)}")
        return jsonify({"error": "Data unavailable. Please try again later."}), 500
    except Exception as e:
        logging.error(f"Unexpected error in get_lora_models: {str(e)}")
        return jsonify({"error": "An unexpected error occurred. Please try again later."}), 500

def run_indexer():
    scheduler = BackgroundScheduler()
    scheduler.add_job(fetch_and_save_lora_models, 'interval', hours=2)
    scheduler.start()

if __name__ == "__main__":
    ensure_lora_models_file()  # Ensure the file exists on startup
    run_indexer()
    app.run(debug=True, use_reloader=False)

# Add this handler for Vercel
from http.server import BaseHTTPRequestHandler

class handler(BaseHTTPRequestHandler):
    def do_GET(self):
        app.run()
        self.send_response(200)
        self.send_header('Content-type', 'text/plain')
        self.end_headers()
        self.wfile.write('Hello, Vercel!'.encode())
        return
