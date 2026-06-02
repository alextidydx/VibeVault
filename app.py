import asyncio
import json
import os
import re
import sqlite3
import sys
import threading
import time
from io import BytesIO

import easyocr
import faiss
import numpy as np
import psutil
import torch
from decord import VideoReader, cpu
from flask import Flask, request, send_file, send_from_directory
from PIL import Image
from rapidfuzz import fuzz
from sentence_transformers import SentenceTransformer
from tqdm.auto import tqdm
from watchdog.observers import Observer
from watchdog.events import FileSystemEventHandler

os.environ['HF_HUB_DISABLE_SYMLINKS_WARNING'] = '1'

db_path = 'files.db'
emb_path = 'files_e.index'
conn = None
cursor = None
default_search_number = 24
url_images = './images'
image_exts = ('.png', '.jpg', '.jpeg', '.jfif')
vid_exts = ('.avi', '.mp4', '.mpeg4', '.mov', '.mkv')
gif_exts = '.gif'

DATA_NOT_PROCESSED = 0
DATA_PROCESSED = 1
DATA_CORRUPTED = 2

host = '0.0.0.0'
port = 5002
public_folder = 'public'
http_thread = None
files_watchdog_delay = 60.0
faiss_manager = None
flask_app = Flask(__name__)

device = 'cuda:0' if torch.cuda.is_available() else 'cpu'
torch_dtype = torch.float16 if torch.cuda.is_available() else torch.float32
EMBEDDING_DIM = 768
main_model_url = 'clip-ViT-L-14'

main_model = None
main_model_processing = False
main_model_process_after = False


# Load the CLIP/SentenceTransformer model and report startup memory usage.
def init_model():
    global main_model, main_model_url, EMBEDDING_DIM
    print('-----------------')
    tmp_time = time.time()
    main_model = SentenceTransformer(main_model_url, trust_remote_code=True)
    EMBEDDING_DIM = 768
    print('EMBEDDING_DIM = ', EMBEDDING_DIM)
    print(f'Model loaded in {time.time() - tmp_time:.2f} seconds')
    pid = os.getpid()
    python_process = psutil.Process(pid)
    memory_use = python_process.memory_info().rss / 2 ** 30
    print(f'Memory usage: {memory_use:.2f} GB')
    print('-----------------')
    return True


# Normalize an embedding vector without producing NaNs for empty vectors.
def safe_normalize(emb):
    """Safe L2 normalization that won't break on zero vectors"""
    norm = np.linalg.norm(emb)
    if norm < 1e-08:
        return np.zeros_like(emb, dtype=np.float32)
    return emb / norm


# Encode a file path or PIL image into a normalized image embedding.
def get_image_emb(image_input):
    global main_model
    try:
        if isinstance(image_input, str):
            image = Image.open(image_input).convert('RGB')
        elif isinstance(image_input, Image.Image):
            image = image_input
        else:
            raise TypeError(f'Expected str or PIL.Image, got {type(image_input)}')
        embedding = main_model.encode(image, normalize_embeddings=True)
        return embedding.astype(np.float32)
    except Exception as e:
        print(f'Error processing image {image_input}: {e}')
        raise


# Encode a text query into a normalized text embedding.
def get_text_emb(text):
    global main_model
    try:
        embedding = main_model.encode(text, normalize_embeddings=True)
        return embedding.astype(np.float32)
    except Exception as e:
        print(f"Error encoding text '{text}': {e}")
        raise


# Return cosine similarity for two already-normalized embeddings.
def get_similarity(emb1, emb2):
    return float(np.dot(emb1, emb2))


reader_ocr = None


# Load the OCR reader used for extracting text from images and video frames.
def init_ocr():
    global reader_ocr
    print('-----------------')
    print('init_ocr')
    tmp_time = time.time()
    reader_ocr = easyocr.Reader(['en'], gpu=torch.cuda.is_available())
    print(f'OCR model loaded in {time.time() - tmp_time:.2f} seconds')
    pid = os.getpid()
    python_process = psutil.Process(pid)
    memory_use = python_process.memory_info().rss / 2 ** 30
    print(f'Memory usage: {memory_use:.2f} GB')
    print('-----------------')


# Run OCR against an image-like input and return cleaned detected text.
def run_ocr_on_pil(image_input: Image.Image):
    if isinstance(image_input, str):
        image = Image.open(image_input).convert('RGB')
        image_np = np.array(image)
    elif isinstance(image_input, Image.Image):
        image_np = np.array(image_input.convert('RGB'))
    elif isinstance(image_input, np.ndarray):
        image_np = image_input
        if len(image_np.shape) == 2:
            image_np = np.stack([image_np] * 3, axis=-1)
        elif image_np.shape[2] == 4:
            image_np = image_np[:, :, :3]
    else:
        print(f'Unsupported image input type: {type(image_input)}')
        return ''
    result = reader_ocr.readtext(image_np, detail=1, paragraph=False)
    texts = []
    for detection in result:
        text = detection[1]
        confidence = detection[2]
        if confidence > 0.4:
            texts.append(text.strip())
    full_text = ' | '.join(texts)
    return full_text.strip()


# Open the SQLite database and ensure the media metadata table exists.
def init_db():
    global conn, cursor
    print('-----------------')
    print('init_db')
    conn = sqlite3.connect(db_path, check_same_thread=False)
    cursor = conn.cursor()
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS files (
            id              INTEGER,
            filename        TEXT NOT NULL UNIQUE,
            desc            TEXT,
            text_ocr        TEXT,
            last_modified   REAL,
            folder          INTEGER,
            duration        REAL,
            processed       INTEGER DEFAULT 0,
            content_type    TEXT,
            custom_modified REAL,
            rating          REAL,
            PRIMARY KEY("id" AUTOINCREMENT)
        )
    """)
    conn.commit()
    return True


# Manage persistent Faiss vectors keyed by media file id.
class FaissIndexManager:

    # Initialize index paths and load or create the Faiss index.
    def __init__(self, index_path='embeddings.index', dim=1024):
        self.index_path = index_path
        self.dim = dim
        self.mapping_path = index_path + '.mapping.json'
        self.index = None
        self.load_or_create()

    # Load an existing Faiss index or create a fresh ID-mapped index.
    def load_or_create(self):
        if os.path.exists(self.index_path):
            print(f'✅ Loading Faiss index: {self.index_path}')
            self.index = faiss.read_index(self.index_path)
        else:
            print('🆕 Creating new Faiss index...')
            quantizer = faiss.IndexFlatIP(self.dim)
            self.index = faiss.IndexIDMap2(quantizer)
            self.save()

    # Replace all vectors for a file id and persist the updated index.
    def add(self, file_id: int, embedding_data):
        if embedding_data is None:
            return
        try:
            if isinstance(embedding_data, (bytes, bytearray)):
                embeddings = np.frombuffer(embedding_data, dtype=np.float32)
            else:
                embeddings = np.asarray(embedding_data, dtype=np.float32)
            num_frames = int(len(embeddings) / self.dim)
            if num_frames < 1:
                return
            vectors = embeddings.reshape(num_frames, self.dim).astype(np.float32)
            self.remove(file_id)
            ids = np.full(num_frames, file_id, dtype=np.int64)
            self.index.add_with_ids(vectors, ids)
            self.save()
        except Exception as e:
            print(f'Error adding file_id {file_id} to Faiss: {e}')

    # Remove all vectors attached to one file id from the index.
    def remove(self, file_id: int):
        """Remove all vectors belonging to this file_id"""
        try:
            ids_to_remove = np.array([file_id], dtype=np.int64)
            selector = faiss.IDSelectorBatch(len(ids_to_remove), faiss.swig_ptr(ids_to_remove))
            self.index.remove_ids(selector)
        except Exception as e:
            pass

    # Search all indexed vectors and collapse frame hits to unique files.
    def search(self, query_embedding):
        query = np.asarray(query_embedding, dtype=np.float32).reshape(1, -1)
        norm = np.linalg.norm(query)
        if norm > 1e-08:
            query = query / norm
        distances, indices = self.index.search(query, self.index.ntotal)
        print('--- Faiss Raw Output ---')
        print(f'Total vectors in index: {self.index.ntotal}')
        print(f'Total files in index: {self.get_unique_file_count()}')
        print(f'Max distance : {distances[0].max():.4f}')
        print(f'Min distance : {distances[0].min():.4f}')
        print(f'Number of valid results: {np.sum(indices[0] != -1)}')
        print('------------------------')
        best = {}
        for dist, idx in zip(distances[0], indices[0]):
            if idx == -1:
                continue
            fid = int(idx)
            sim = float(dist)
            if fid not in best or sim > best[fid]:
                best[fid] = sim
        results = [{'id': fid, 'similarity': sim} for fid, sim in best.items()]
        results.sort(key=lambda x: x['similarity'], reverse=True)
        print(f'Returning {len(results)} unique files')
        return results

    # Persist the current Faiss index to disk.
    def save(self):
        faiss.write_index(self.index, self.index_path)

    # Estimate the number of unique files represented in the index.
    def get_unique_file_count(self):
        if self.index is None or self.index.ntotal == 0:
            return 0
        try:
            dummy = np.zeros((1, self.dim), dtype=np.float32)
            _, indices = self.index.search(dummy, self.index.ntotal)
            unique = len(set((int(x) for x in indices[0] if x != -1)))
            return unique
        except:
            return self.index.ntotal


# Read runtime settings from config.json into module-level state.
def load_config():
    global db_path, default_search_number, files_watchdog_delay
    global host, image_exts, main_model_url, port, public_folder, url_images, vid_exts

    try:
        with open('config.json') as f:
            d = json.load(f)
        main_model_url = d.get('model_desc_url')
        url_images = d.get('folders')[0].get('path')
        image_exts = tuple(d.get('image_exts'))
        vid_exts = tuple(d.get('vid_exts'))
        db_path = d.get('db_path')
        host = d.get('host')
        port = d.get('port')
        public_folder = d.get('public_folder')
        default_search_number = d.get('default_search_number')
        files_watchdog_delay = float(d.get('files_watchdog_delay'))
    except Exception as e:
        print(f'Error in config: {e}')


# Fail fast when the configured media folder is missing.
def check_folder_existence():
    if not os.path.isdir(url_images):
        print(f"Folder wasn't found")
        sys.exit()


# Remove database and index records for media files no longer on disk.
def check_missing_files():
    print('-----------------')
    print('Check if the files exist')
    cursor.execute('SELECT id, filename FROM files')
    files = cursor.fetchall()
    missing_files = 0
    for file_id, filename in files:
        if not os.path.exists(os.path.join(url_images, filename).replace('\\', '/')):
            missing_files += 1
            cursor.execute('DELETE FROM files WHERE id = ?', (file_id,))
            if faiss_manager:
                faiss_manager.remove(file_id)
    conn.commit()
    print(f'Missing: {missing_files}')


# Classify a path as image, video, gif, or unknown from its extension.
def get_content_type(filepath):
    filepath_lower = filepath.lower()
    if filepath_lower.endswith(image_exts):
        return 'image'
    elif filepath_lower.endswith(vid_exts):
        return 'video'
    elif filepath_lower.endswith(gif_exts):
        return 'gif'
    return 'unknown'


# Discover new supported media files and add metadata rows for them.
def check_new_files():
    print('-----------------')
    print('Adding new media ...')
    files_added = 0
    for root, dirs, files in os.walk(url_images):
        for file in files:
            file_path = os.path.join(root, file).replace('\\', '/')[len(url_images) + 1:]
            last_modified = time.time()
            content_type = get_content_type(file_path)
            if content_type != 'unknown':
                cursor.execute('SELECT id FROM files WHERE filename = ?', (file_path,))
                results = cursor.fetchall()
                if len(results) <= 0:
                    print('Add ' + content_type + ': ', files_added, file_path)
                    cursor.execute(
                        """
                        INSERT INTO files (
                            filename, content_type, last_modified, rating
                        )
                        VALUES (?, ?, ?, ?)
                        """,
                        (file_path, content_type, last_modified, -1.0),
                    )
                    files_added += 1
    conn.commit()
    print(f'Files added total: {files_added}')


# Normalize OCR text into a compact searchable string.
def clean_ocr_text(text: str) -> str:
    """Clean OCR text and return empty string if nothing meaningful remains"""
    if not text:
        return None
    t = str(text).strip()
    t = re.sub('[;.,|_\\-!@#$%^&*()+=[\\]{}:"\\\'`<>/\\\\?]', ' ', t)
    t = re.sub('\\s+', ' ', t).strip()
    if len(t) == 0 or len(t) < 2:
        return None
    t = str(text).strip()
    t = re.sub('[;|]', ' ', t)
    t = re.sub('\\s+', ' ', t).strip()
    return t


# Choose how many video frames to sample based on media duration.
def frames_to_get(_duration):
    result = (1, 1)
    if _duration == 0.0:
        result = (1, 1)
    elif _duration < 10.0:
        result = (1, 1)
    elif _duration < 20.0:
        result = (2, 1)
    elif _duration < 30.0:
        result = (2, 2)
    elif _duration < 2 * 60.0:
        result = (3, 2)
    elif _duration < 5 * 60.0:
        result = (3, 3)
    elif _duration < 10 * 60.0:
        result = (4, 3)
    elif _duration < 30 * 60.0:
        result = (5, 4)
    else:
        result = (6, 4)
    print('Duration: ', _duration, result)
    return result


# Extract embeddings, OCR text, and duration metadata from one media file.
def scrabble_media(filepath):
    output = {'type': 'unknown', 'embedding': None, 'duration': None, 'text_ocr': ''}
    content_type = get_content_type(filepath)
    if os.path.exists(filepath):
        text_ocr = ''
        if content_type == 'image':
            print('Image: ', filepath)
            image = Image.open(filepath).convert('RGB')
            embedding = get_image_emb(image)
            text_ocr = run_ocr_on_pil(image)
        elif content_type == 'video' or content_type == 'gif':
            print('Animation: ', filepath)
            embeddings_blob = bytearray()
            fps = 0
            try:
                vr = VideoReader(filepath, ctx=cpu(0))
                totalF = len(vr)
                fps = vr.get_avg_fps()
                if np.isnan(fps):
                    fps = 0
                duration = 0.0
                if fps != 0:
                    duration = float(totalF / fps)
                frame_config = frames_to_get(duration)
                shiftHalf = int(totalF / (2 * (frame_config[0] + 1)))
                shift = shiftHalf * 2
                shiftAuxKShift = int(0.6 * shift / frame_config[1])
                shiftAux0 = int(0.5 * shiftAuxKShift)
                for mainI in range(frame_config[0]):
                    curMainPos = shiftHalf + mainI * shift
                    embeddingInterp = np.zeros(EMBEDDING_DIM, dtype=np.float32)
                    for auxI in range(frame_config[1]):
                        curFramePos = curMainPos - shiftAux0 + auxI * shiftAuxKShift
                        framesRead = 0
                        try:
                            frame = vr[curFramePos]
                            image = frame.asnumpy()
                            imageRGB = Image.fromarray(image, mode='RGB')
                            embedding = get_image_emb(imageRGB)
                            text_ocr += '; ' + run_ocr_on_pil(image)
                            embeddingInterp += embedding
                            framesRead += 1
                        except Exception as e:
                            print(f"(!) cant'r read frame {curFramePos}", filepath, e)
                    if np.linalg.norm(embeddingInterp) > 0.1:
                        embeddingInterp = safe_normalize(embeddingInterp)
                        embeddings_blob += embeddingInterp.tobytes()
                print(' embeddings_blob len = ', len(embeddings_blob))
                if len(embeddings_blob) > 0:
                    embedding = embeddings_blob
                else:
                    print('(!) CORRUPTED_EMBEDDING_MARKER')
                    embedding = None
                output['duration'] = duration
            except Exception as e:
                print('(!) Corrupted video or gif', filepath)
                print('(!) CORRUPTED_EMBEDDING_MARKER')
                embedding = None
                print(e)
        output['type'] = content_type
        output['embedding'] = embedding
        output['text_ocr'] = clean_ocr_text(text_ocr)
    return output


# Process unindexed media rows and write embeddings plus OCR metadata.
def check_missing_meta():
    print('-----------------')
    print('Processing files for Faiss ...')
    cursor.execute("""
        SELECT id, filename, content_type
        FROM files
        WHERE processed = ?
    """, (DATA_NOT_PROCESSED,))
    files = cursor.fetchall()
    print(f'Found {len(files)} files')
    if len(files) == 0:
        return
    tmpTime = time.time()
    curFileN = 0
    pbar = tqdm(total=len(files))
    for file_id, filename, content_type in files:
        file_path = os.path.join(url_images, filename).replace('\\', '/')
        try:
            media_data = scrabble_media(file_path)
            if media_data.get('embedding') is not None and faiss_manager:
                faiss_manager.add(file_id, media_data['embedding'])
                cursor.execute(
                    """
                    UPDATE files
                    SET processed = ?, text_ocr = ?, duration = ?
                    WHERE id = ?
                    """,
                    (
                        DATA_PROCESSED,
                        media_data.get('text_ocr', ''),
                        media_data.get('duration', None),
                        file_id,
                    ),
                )
                conn.commit()
                print(f'✓ Processed: {filename}')
            else:
                cursor.execute(
                    'UPDATE files SET text_ocr = NULL, processed = ? WHERE id = ?',
                    (DATA_CORRUPTED, file_id),
                )
                conn.commit()
                print(f'✗ Corrupted: {filename}')
        except Exception as e:
            cursor.execute(
                'UPDATE files SET text_ocr = NULL, processed = ? WHERE id = ?',
                (DATA_CORRUPTED, file_id),
            )
            conn.commit()
            print(f'✗ Failed: {filename} - {e}')
        curFileN += 1
        pbar.update(1)
    pbar.close()
    print(f'Processed in: {time.time() - tmpTime:.2f} second(s))')
    print(f'Files processed: {curFileN}')
    print('Faiss processing finished')
    print('-----------------')


# Run the full media sync: remove missing files, add new files, then process metadata.
def check_all_files_and_meta():
    check_missing_files()
    check_new_files()
    check_missing_meta()


# Score how strongly a text query matches a media filename.
def filename_similarity(query: str, filename: str) -> float:
    if not query or not filename:
        return 0.0
    filename = os.path.basename(filename)
    q = query.lower().strip()
    f = filename.lower().replace('_', ' ').replace('-', ' ')
    score1 = fuzz.ratio(q, f)
    score2 = fuzz.partial_ratio(q, f)
    score3 = fuzz.token_sort_ratio(q, f)
    best_score = max(score1, score2, score3)
    return best_score / 100.0


# Return media ids sorted by a database column for recent/favorite views.
def get_sorted_indices(sort_by, order):
    json_output = {'status': 'in process', 'images': []}
    img_conn = sqlite3.connect(db_path, check_same_thread=False)
    with img_conn:
        img_cur = img_conn.cursor()
        img_cur.execute(
            'SELECT id, ' + sort_by + ' FROM files ORDER BY ' + sort_by + ' ' + order.upper()
        )
        results = img_cur.fetchall()
    img_conn.close()
    for id_val, column in results:
        elem = {}
        elem['id'] = int(id_val)
        elem[sort_by] = column
        json_output['images'].append(elem)
    json_output['status'] = 'ok'
    return json_output


# Build ranked search results from filename, OCR, and semantic Faiss matches.
def get_search_indices(query):
    global faiss_manager
    print('-----------------')
    print(f'Searching for: {query}')
    json_output = {'status': 'in process', 'images': []}
    try:
        if isinstance(query, str):
            if len(query) < 2:
                json_output['status'] = 'short query'
                return json_output
            final_results = []
            cursor.execute('SELECT id, filename FROM files')
            all_files = cursor.fetchall()
            strong_filename = []
            for file_id, filename in all_files:
                score = filename_similarity(query, filename)
                if score >= 0.75:
                    print('filename search: ', os.path.basename(filename))
                    strong_filename.append({
                        'id': file_id,
                        'similarity': score,
                        'source': 'filename',
                    })
            strong_filename.sort(key=lambda x: x['similarity'], reverse=True)
            final_results.extend(strong_filename)
            print('strong_filename = ', len(strong_filename))
            used_ids = {item['id'] for item in strong_filename}
            strong_ocr = []
            if used_ids:
                placeholders = ','.join(['?'] * len(used_ids))
                cursor.execute(f"""
                    SELECT id, filename, text_ocr
                    FROM files
                    WHERE text_ocr IS NOT NULL
                      AND text_ocr != ''
                      AND id NOT IN ({placeholders})
                """, list(used_ids))
            else:
                cursor.execute("""
                    SELECT id, filename, text_ocr
                    FROM files
                    WHERE text_ocr IS NOT NULL AND text_ocr != ''
                """)
            ocr_candidates = cursor.fetchall()
            print('ocr_candidates = ', len(ocr_candidates))
            for file_id, filename, text_ocr in ocr_candidates:
                ocr_score = fuzz.partial_ratio(query.lower(), text_ocr.lower()) / 100.0
                if ocr_score >= 0.75:
                    strong_ocr.append({
                        'id': file_id,
                        'similarity': ocr_score,
                        'source': 'ocr',
                    })
            strong_ocr.sort(key=lambda x: x['similarity'], reverse=True)
            print('strong_ocr = ', len(strong_ocr))
            final_results.extend(strong_ocr)
            used_ids.update((item['id'] for item in strong_ocr))
            query_embedding = get_text_emb(query)
            faiss_results = faiss_manager.search(query_embedding)
            remaining_faiss = [item for item in faiss_results if item['id'] not in used_ids]
            final_results.extend(remaining_faiss)
            json_output['status'] = 'ok'
            json_output['images'] = final_results[:default_search_number]
        elif isinstance(query, Image.Image):
            query_embedding = get_image_emb(query)
            similarities = faiss_manager.search(query_embedding)
            json_output['status'] = 'ok'
            json_output['images'] = similarities[:default_search_number]
    except Exception as e:
        print(f'Search error: {e}')
        json_output['status'] = 'error'
        json_output['message'] = str(e)
    return json_output


# Fetch requested metadata fields for a list of media ids.
def get_more_info(ids, requested_fields):
    print('-----------------')
    print(f'More info search for')
    json_output = {'status': 'in process', 'images': []}
    if not ids:
        json_output['status'] = 'error'
        json_output['message'] = 'No ids provided'
        return json_output
    always_fields = ['id', 'filename']
    allowed_fields = [
        'desc',
        'content_type',
        'custom_modified',
        'rating',
        'duration',
        'text_ocr',
        'last_modified',
    ]
    fields = always_fields.copy()
    for field in requested_fields:
        if field in allowed_fields and field not in fields:
            fields.append(field)
    try:
        img_conn = sqlite3.connect(db_path, check_same_thread=False)
        with img_conn:
            img_cur = img_conn.cursor()
            select_clause = ', '.join(fields)
            placeholders = ','.join(['?'] * len(ids))
            query = f'SELECT {select_clause} FROM files WHERE id IN ({placeholders})'
            img_cur.execute(query, ids)
            results = img_cur.fetchall()
        images = []
        for row in results:
            item = {}
            for i, field in enumerate(fields):
                item[field] = row[i]
            images.append(item)
        json_output['status'] = 'ok'
        json_output['images'] = images
    except Exception as e:
        print(f'get-details error: {e}')
        json_output['status'] = 'error'
        json_output['message'] = str(e)
    return json_output


# Serve the single-page app entry point for root and client-side routes.
@flask_app.route('/')
@flask_app.route('/search/')
@flask_app.route('/search-image/')
def send_index():
    public_folder_absolute = os.path.join(os.getcwd(), public_folder)
    print('index')
    return send_from_directory(public_folder_absolute, 'index.html')


# Stream the original media file for a database id.
@flask_app.route('/api/img/<int:img_id>')
def send_img(img_id):
    img_conn = sqlite3.connect(db_path, check_same_thread=False)
    public_folder_absolute = os.path.join(os.getcwd(), public_folder)
    json_output = {'status': 'in process'}
    with img_conn:
        img_cur = img_conn.cursor()
        img_cur.execute('SELECT id, filename, content_type FROM files WHERE id = ?', (img_id,))
        files = img_cur.fetchall()
    img_conn.close()
    if len(files) > 0:
        file_path = os.path.join(url_images, files[0][1]).replace('\\', '/')
        if not os.path.exists(file_path):
            json_output['status'] = 'error 404'
            return json_output
        if files[0][2] == 'image':
            json_output['status'] = 'ok'
            return send_file(file_path)
        if files[0][2] == 'gif':
            json_output['status'] = 'ok'
            return send_file(file_path)
        elif files[0][2] == 'video':
            json_output['status'] = 'ok'
            return send_file(file_path)
        json_output['status'] = 'format is not supported'
        return json_output
    else:
        json_output['status'] = 'error 404'
        return json_output


# Handle text search requests.
@flask_app.route('/api/search/')
def search_query():
    query = request.args.get('query', '')
    print('>Search for : ', query)
    return get_search_indices(query)


# Handle image upload search requests.
@flask_app.route('/api/search-image/', methods=['POST'])
def search_query_image():
    file = request.files['image']
    if file.filename == '' or not file.content_type.startswith('image/'):
        return ({'status': 'error', 'message': 'Invalid image'}, 400)
    try:
        image = Image.open(BytesIO(file.read())).convert('RGB')
        return get_search_indices(image)
    except Exception as e:
        print('Image search error:', e)
        return ({'status': 'error', 'message': str(e)}, 500)


# Return media ids ordered by latest modification time.
@flask_app.route('/api/recent/')
def recent_query():
    print('recent')
    return get_sorted_indices('last_modified', 'desc')


# Return media ids ordered by highest rating.
@flask_app.route('/api/favorites/')
def favorites_query():
    print('favorites')
    return get_sorted_indices('rating', 'desc')


# Return file metadata needed by the frontend result cards.
@flask_app.route('/api/get-details/')
def get_details():
    json_output = {'status': 'in process', 'images': []}
    ids = request.args.getlist('ids[]')
    requested_fields = request.args.getlist('fields[]')
    if requested_fields:
        fields = [
            f for f in requested_fields
            if f in [
                'id',
                'filename',
                'desc',
                'content_type',
                'custom_modified',
                'rating',
                'text_ocr',
                'duration',
                'last_modified',
            ]
        ]
    else:
        fields = ['id', 'filename', 'desc', 'content_type', 'custom_modified', 'rating']
    return get_more_info(ids, fields)


# Rebuild the Faiss index from database records.
@flask_app.route('/api/rebuild-faiss/')
def rebuild_faiss():
    global faiss_manager
    if faiss_manager is None:
        return {'status': 'error', 'message': 'Faiss manager not initialized'}
    try:
        faiss_manager.rebuild_from_db(cursor)
        return {'status': 'ok', 'message': 'Faiss index rebuilt successfully'}
    except Exception as e:
        return {'status': 'error', 'message': str(e)}


# Persist a user-edited description for one media item.
@flask_app.route('/api/set-desc/<int:img_id>')
def set_desc(img_id):
    json_output = {'status': 'in process', 'desc': ''}
    desc = str(request.args.get('desc') or '')
    img_cnn = sqlite3.connect(db_path, check_same_thread=False)
    custom_modified = time.time()
    with img_cnn:
        img_cur = img_cnn.cursor()
        img_cur.execute(
            'UPDATE files SET desc = ?, custom_modified = ? WHERE id = ?',
            (desc, custom_modified, img_id),
        )
        updated_rows = img_cur.rowcount
        if updated_rows > 0:
            json_output['status'] = 'ok'
            json_output['desc'] = desc
        else:
            json_output['status'] = 'failed'
    img_cnn.close()
    return json_output


# Persist a user rating for one media item.
@flask_app.route('/api/set-rating/<int:img_id>')
def set_rating(img_id):
    json_output = {'status': 'in process', 'desc': ''}
    rating = max(0.0, min(10.0, float(request.args.get('rating') or 0)))
    img_conn = sqlite3.connect(db_path, check_same_thread=False)
    custom_modified = time.time()
    with img_conn:
        img_cur = img_conn.cursor()
        img_cur.execute(
            'UPDATE files SET rating = ?, custom_modified = ? WHERE id = ?',
            (rating, custom_modified, img_id),
        )
        updated_rows = img_cur.rowcount
        if updated_rows > 0:
            json_output['status'] = 'ok'
            json_output['rating'] = rating
        else:
            json_output['status'] = 'failed'
    img_conn.close()
    return json_output


# Delete one media file and remove its database/index records.
@flask_app.route('/api/delete/<int:img_id>')
def delete_media(img_id):
    json_output = {'status': 'in process', 'desc': ''}
    img_conn = sqlite3.connect(db_path, check_same_thread=False)
    with img_conn:
        img_cur = img_conn.cursor()
        img_cur.execute('SELECT id, filename, content_type FROM files WHERE id = ?', (img_id,))
        files = img_cur.fetchall()
        updated_rows = img_cur.rowcount
    if updated_rows == 0:
        img_conn.close()
        json_output['status'] = 'failed'
        return json_output
    file_path = os.path.join(url_images, files[0][1]).replace('\\', '/')
    if os.path.exists(file_path):
        print('Remove file:', file_path)
        os.remove(file_path)
    img_cur.execute('DELETE FROM files WHERE id = ?', (img_id,))
    img_conn.commit()
    if faiss_manager:
        faiss_manager.remove(img_id)
    if updated_rows > 0:
        json_output['status'] = 'ok'
    else:
        json_output['status'] = 'failed'
    img_conn.close()
    return json_output


# Clear a custom description and mark the media item as unmodified.
@flask_app.route('/api/clear-desc/<int:img_id>')
def clear_desc(img_id):
    json_output = {'status': 'in process'}
    img_conn = sqlite3.connect(db_path, check_same_thread=False)
    with img_conn:
        img_cur = img_conn.cursor()
        img_cur.execute('SELECT id, filename, content_type FROM files WHERE id = ?', (img_id,))
        files = img_cur.fetchall()
        updated_rows = img_cur.rowcount
    if updated_rows == 0:
        img_conn.close()
        json_output['status'] = 'failed'
        return json_output
    file_path = os.path.join(url_images, files[0][1]).replace('\\', '/')
    media_data = scrabble_media(file_path)
    with img_conn:
        img_cur = img_conn.cursor()
        img_cur.execute(
            'UPDATE files SET desc = ?, custom_modified = ? WHERE id = ?',
            ('', None, img_id),
        )
        updated_rows = img_cur.rowcount
        if updated_rows > 0:
            json_output['status'] = 'ok'
        else:
            json_output['status'] = 'failed'
    img_conn.close()
    return json_output


# Trigger a full media rescan from the API.
@flask_app.route('/api/recheck/')
def recheck():
    json_output = {'status': 'in process'}
    try:
        check_all_files_and_meta()
        json_output['status'] = 'ok'
    except Exception as e:
        json_output['status'] = f'error {e}'
    return json_output


# Serve built frontend assets from the configured public folder.
@flask_app.route('/<path:path>')
def send_files(path):
    public_folder_absolute = os.path.join(os.getcwd(), public_folder)
    return send_from_directory(public_folder_absolute, path)


# Debounce filesystem events before triggering a media rescan.
class file_change_handler(FileSystemEventHandler):
    global files_watchdog_delay, main_model_processing, main_model_process_after

    # Capture the event loop used to schedule debounce workers.
    def __init__(self):
        self.loop = asyncio.get_event_loop()
        self.worker_task = None
        print('FileChange Init()')

    # Schedule or reset a delayed rescan for file changes.
    def on_any_event(self, event):
        if main_model_processing:
            main_model_process_after = True
            return
        if event.is_directory:
            return
        if self.worker_task is not None:
            self.worker_task.cancel()
            self.worker_task = None
        self.worker_task = asyncio.run_coroutine_threadsafe(self.debounce_worker(), self.loop)

    # Wait for filesystem changes to settle, then rescan media.
    async def debounce_worker(self):
        global files_watchdog_delay
        try:
            await asyncio.sleep(files_watchdog_delay)
            print('Syncing ...')
            self.worker_task = None
            check_all_files_and_meta()
        except asyncio.CancelledError:
            print('Scheduled syncing reinitiated')


# Initialize services, start the watchdog observer, and run Flask forever.
async def start_monitoring():
    global handler, model_desc_enabled, http_thread, host, port, faiss_manager
    init_db()
    init_ocr()
    init_model()
    faiss_manager = FaissIndexManager(index_path=emb_path, dim=EMBEDDING_DIM)
    handler = file_change_handler()
    observer = Observer()
    observer.schedule(handler, url_images, recursive=True)
    observer.start()
    check_all_files_and_meta()
    try:
        http_thread = threading.Thread(
            target=lambda: flask_app.run(host=host, port=port, threaded=True)
        )
        http_thread.start()
        await asyncio.Future()
    except asyncio.CancelledError:
        observer.stop()
        observer.join()
        if http_thread:
            http_thread.join(timeout=1.0)
        conn.close()
        print('Daemon stopped.')


if __name__ == '__main__':
    load_config()
    try:
        asyncio.run(start_monitoring())
    except KeyboardInterrupt:
        print('Program interrupted')
    except Exception as e:
        print('Error in asyncio.run:', e)
    finally:
        print('Program exited')
        os._exit(0)
