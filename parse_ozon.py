"""
Парсер карточек товаров Ozon

Назначение:
    Парсинг карточек товаров с сайта Ozon по списку SKU.
    Извлечение информации о товарах: название, цена, рейтинг, отзывы,
    изображения, характеристики (цвет, материал, артикул) и наличие
    Rich Content в описании.

Зависимости:
    - selenium
    - requests (для работы с cookies)

Входные данные:
    - cookies_file: JSON-файл с cookies для авторизации
    - SKUS: список артикулов товаров для парсинга

Выходные данные:
    - CSV-файл с результатами парсинга
    - JSON-файл с резервной копией результатов
    - Лог-файл с деталями работы
"""

import os
import re
import csv
import json
import time
import random
import logging
from typing import List, Dict, Optional, Any

# Импорт Selenium для работы с браузером
from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.chrome.options import Options
from selenium.common.exceptions import TimeoutException


# ============================================================================
# НАСТРОЙКИ
# ============================================================================

# Настройка логирования
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler('parser.log', encoding='utf-8'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

# Конфигурационные константы
COOKIES_FILE = 'ozon_data_cookies.json'          # Файл с cookies для авторизации
OUTPUT_CSV = 'ozon_products.csv'                 # Имя выходного CSV-файла
OUTPUT_JSON = 'ozon_products.json'               # Имя выходного JSON-файла
USER_AGENT = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) ' \
             'AppleWebKit/537.36 (KHTML, like Gecko) ' \
             'Chrome/131.0.0.0 Safari/537.36'

# Список SKU для парсинга (можно изменить)
DEFAULT_SKUS = ['2359066702', '2829800382']


# ============================================================================
# РАБОТА С COOKIES
# ============================================================================

def load_cookies(path: str) -> List[Dict]:
    """
    Загрузка и форматирование cookies из JSON-файла для использования в Selenium.

    Args:
        path: Путь к файлу с cookies

    Returns:
        List[Dict]: Список cookies в формате Selenium
    """
    # Проверка существования файла
    if not os.path.exists(path):
        logger.error(f"❌ Файл cookies не найден: {path}")
        return []

    try:
        with open(path, 'r', encoding='utf-8') as f:
            raw_data = json.load(f)

        formatted_cookies = []

        # Преобразование в формат Selenium
        if isinstance(raw_data, list):
            # Если файл содержит список cookies (стандартный формат)
            formatted_cookies = raw_data
        elif isinstance(raw_data, dict):
            # Если файл содержит словарь name->value
            for name, value in raw_data.items():
                if name and value:
                    formatted_cookies.append({
                        "name": name,
                        "value": str(value),
                        "domain": ".ozon.ru",
                        "path": "/",
                        "secure": True
                    })

        logger.info(f"🍪 Загружено {len(formatted_cookies)} cookies")
        return formatted_cookies

    except json.JSONDecodeError as e:
        logger.error(f"❌ Ошибка парсинга JSON в файле cookies: {e}")
        return []
    except Exception as e:
        logger.error(f"❌ Ошибка чтения файла cookies: {e}")
        return []


def setup_chrome_driver() -> webdriver.Chrome:
    """
    Настройка Chrome драйвера с опциями для обхода защиты.

    Returns:
        webdriver.Chrome: Настроенный экземпляр драйвера
    """
    options = Options()

    # Отключение автоматизации для обхода защиты
    options.add_argument('--disable-blink-features=AutomationControlled')
    options.add_experimental_option("excludeSwitches", ["enable-automation"])
    options.add_experimental_option('useAutomationExtension', False)

    # Стандартные опции для стабильной работы
    options.add_argument('--no-sandbox')
    options.add_argument('--disable-dev-shm-usage')
    options.add_argument('--start-maximized')
    options.add_argument('--disable-notifications')
    options.add_argument(f'user-agent={USER_AGENT}')

    # Создание драйвера
    driver = webdriver.Chrome(options=options)

    # Скрытие флага webdriver
    driver.execute_script(
        "Object.defineProperty(navigator, 'webdriver', {get: () => undefined})"
    )

    return driver


def initialize_session(driver: webdriver.Chrome, cookies: List[Dict]) -> bool:
    """
    Инициализация сессии с применением cookies для авторизации.

    Args:
        driver: Экземпляр Selenium драйвера
        cookies: Список cookies для установки

    Returns:
        bool: True если сессия успешно инициализирована
    """
    try:
        # Переход на главную страницу Ozon
        driver.get('https://www.ozon.ru/')
        time.sleep(2)
        logger.debug("Открыта главная страница Ozon")

        # Установка cookies
        for cookie in cookies:
            try:
                driver.add_cookie(cookie)
            except Exception as e:
                logger.debug(f"Не удалось установить cookie {cookie.get('name')}: {e}")

        # Обновление страницы для применения cookies
        driver.refresh()
        time.sleep(3)

        logger.info("✅ Сессия инициализирована с cookies")
        return True

    except Exception as e:
        logger.error(f"❌ Ошибка инициализации сессии: {e}")
        return False


# ============================================================================
# ПАРСИНГ ДАННЫХ
# ============================================================================

def extract_json_ld(html: str) -> Optional[Dict[str, Any]]:
    """
    Извлечение структурированных данных JSON-LD из HTML.

    JSON-LD содержит основную информацию о товаре в структурированном виде.

    Args:
        html: HTML-код страницы

    Returns:
        Optional[Dict]: Словарь с данными или None
    """
    try:
        # Поиск всех script-тегов с типом application/ld+json
        pattern = r'<script[^>]*type=["\']application/ld\+json["\'][^>]*>(.*?)</script>'
        match = re.search(pattern, html, re.DOTALL)

        if not match:
            logger.debug("JSON-LD не найден на странице")
            return None

        # Очистка и парсинг JSON
        clean_json = match.group(1).replace('\\"', '"')
        data = json.loads(clean_json)

        # Поиск блока с типом Product
        if isinstance(data, dict) and data.get('@type') == 'Product':
            return data

        if isinstance(data, list):
            for item in data:
                if isinstance(item, dict) and item.get('@type') == 'Product':
                    return item

        return None

    except json.JSONDecodeError as e:
        logger.debug(f"Ошибка парсинга JSON-LD: {e}")
        return None
    except Exception as e:
        logger.debug(f"Неизвестная ошибка при парсинге JSON-LD: {e}")
        return None


def parse_from_json_ld(json_ld: Dict[str, Any], product: Dict) -> Dict:
    """
    Извлечение данных о товаре из JSON-LD структуры.

    Args:
        json_ld: Словарь с данными JSON-LD
        product: Словарь с текущими данными товара

    Returns:
        Dict: Обновленный словарь с данными товара
    """
    # Название товара
    product['title'] = json_ld.get('name', '')

    # Изображения
    images = json_ld.get('image', [])
    if isinstance(images, str):
        product['cover_image'] = images
        product['photos_seller'] = 1
    elif isinstance(images, list):
        if images:
            product['cover_image'] = images[0]
        product['photos_seller'] = len(images)

    # Цена
    offers = json_ld.get('offers', {})
    if isinstance(offers, dict):
        price_val = offers.get('price')
        if price_val is not None:
            try:
                product['price'] = float(str(price_val).replace(',', '.'))
            except (ValueError, TypeError) as e:
                logger.debug(f"Ошибка парсинга цены из JSON-LD: {e}")

    # Рейтинг и количество отзывов
    agg_rating = json_ld.get('aggregateRating', {})
    if isinstance(agg_rating, dict):
        try:
            # Рейтинг
            rating_val = agg_rating.get('ratingValue')
            if rating_val is not None:
                if isinstance(rating_val, (int, float)):
                    product['rating'] = float(rating_val)
                elif isinstance(rating_val, str):
                    clean_rating = re.sub(r'[^\d.]', '', rating_val.replace(',', '.'))
                    if clean_rating:
                        product['rating'] = float(clean_rating)

            # Количество отзывов
            review_count = agg_rating.get('reviewCount')
            if review_count is not None:
                product['reviews_total'] = int(review_count)

        except (ValueError, TypeError) as e:
            logger.debug(f"Ошибка парсинга рейтинга из JSON-LD: {e}")

    # Проверка наличия Rich Content в описании
    description = str(json_ld.get('description', ''))
    product['has_rich_content'] = bool(
        re.search(r'<(img|table|ul|ol)', description, re.IGNORECASE)
    )

    return product


def safe_find_element(driver: webdriver.Chrome,
                      selectors: List[str],
                      timeout: int = 5) -> Optional[Any]:
    """
    Безопасный поиск элемента по списку CSS-селекторов.

    Args:
        driver: Экземпляр Selenium драйвера
        selectors: Список CSS-селекторов для поиска
        timeout: Время ожидания элемента в секундах

    Returns:
        Optional: Найденный элемент или None
    """
    for selector in selectors:
        try:
            element = WebDriverWait(driver, timeout).until(
                EC.presence_of_element_located((By.CSS_SELECTOR, selector))
            )
            if element and element.is_displayed():
                return element
        except TimeoutException:
            continue
        except Exception:
            continue

    return None


def parse_characteristics_advanced(driver: webdriver.Chrome) -> Dict[str, str]:
    """
    Расширенный парсинг характеристик товара с использованием нескольких стратегий.

    Используются следующие стратегии поиска:
    1. JSON-LD структура
    2. Блок характеристик товара
    3. Специальные селекторы цвета
    4. JavaScript скрипты с данными
    5. Описание товара

    Args:
        driver: Экземпляр Selenium драйвера

    Returns:
        Dict: Словарь с найденными характеристиками
    """
    characteristics = {'color': '', 'material': '', 'art_set': ''}

    try:
        # Прокрутка к блоку характеристик
        driver.execute_script("window.scrollTo(0, 600);")
        time.sleep(2)

        # Получение HTML для поиска
        html_content = driver.page_source

        # ------------------------------------------------------------
        # СТРАТЕГИЯ 1: Поиск в JSON-LD
        # ------------------------------------------------------------
        json_ld = extract_json_ld(html_content)
        if json_ld:
            # Проверка наличия нужных полей в JSON-LD
            for prop in ['color', 'material', 'sku', 'mpn', 'model']:
                if prop in json_ld and json_ld[prop]:
                    value = str(json_ld[prop]).strip()

                    if prop == 'color' and not characteristics['color']:
                        characteristics['color'] = value
                        logger.info(f"  🎨 Цвет из JSON-LD: {value}")

                    elif prop == 'material' and not characteristics['material']:
                        characteristics['material'] = value
                        logger.info(f"  📦 Материал из JSON-LD: {value}")

                    elif prop in ['sku', 'mpn', 'model'] and not characteristics['art_set']:
                        # Извлечение цифр из артикула
                        numbers = re.findall(r'\d+', value)
                        if numbers:
                            characteristics['art_set'] = ''.join(numbers)
                            logger.info(f"  🔢 Артикул из JSON-LD: {characteristics['art_set']}")

        # ------------------------------------------------------------
        # СТРАТЕГИЯ 2: Поиск в характеристиках товара
        # ------------------------------------------------------------
        char_selectors = [
            '[data-test-id="product-characteristics"]',
            '.characteristics-block',
            '.attributes-list',
            '.product-attributes',
            '.characteristics'
        ]

        for selector in char_selectors:
            try:
                sections = driver.find_elements(By.CSS_SELECTOR, selector)
                for section in sections:
                    items = section.find_elements(
                        By.CSS_SELECTOR,
                        'div, li, .item, .attribute-item, .characteristic-item'
                    )

                    for item in items:
                        text = item.text.strip()
                        if not text or len(text) < 3:
                            continue

                        # Пропуск служебных текстов
                        skip_words = [
                            'в сравнение', 'поделиться', 'отзыв',
                            'вопрос', 'купить', 'скидка', 'корзина'
                        ]
                        if any(word in text.lower() for word in skip_words):
                            continue

                        # Поиск паттерна "ключ: значение" или "ключ — значение"
                        for separator in [':', '—', '–', '-']:
                            if separator in text:
                                parts = text.split(separator, 1)
                                if len(parts) == 2:
                                    key = parts[0].strip().lower()
                                    value = parts[1].strip()

                                    # Очистка значения
                                    value = re.sub(r'\s+', ' ', value)

                                    # Если значений несколько, берем первое
                                    if ',' in value and not any(
                                        k in key for k in ['артикул', 'арт.']
                                    ):
                                        value = value.split(',')[0].strip()

                                    # Определение типа характеристики
                                    color_keywords = ['цвет', 'color', 'colour']
                                    if any(k in key for k in color_keywords):
                                        if not characteristics['color'] and len(value) > 1:
                                            characteristics['color'] = value
                                            logger.info(f"  🎨 Найден цвет: {value}")

                                    material_keywords = [
                                        'материал', 'material', 'состав', 'composition'
                                    ]
                                    if any(k in key for k in material_keywords):
                                        if not characteristics['material'] and len(value) > 1:
                                            characteristics['material'] = value
                                            logger.info(f"  📦 Найден материал: {value}")

                                    art_keywords = [
                                        'артикул', 'арт.', 'part number', 'model', 'sku'
                                    ]
                                    if any(k in key for k in art_keywords):
                                        if not characteristics['art_set']:
                                            numbers = re.findall(r'\d+', value)
                                            if numbers:
                                                characteristics['art_set'] = ''.join(numbers)
                                                logger.info(
                                                    f"  🔢 Найден артикул: {characteristics['art_set']}"
                                                )

                                    break
            except Exception as e:
                logger.debug(f"Ошибка при поиске в {selector}: {e}")

        # ------------------------------------------------------------
        # СТРАТЕГИЯ 3: Поиск цвета в специальных селекторах
        # ------------------------------------------------------------
        if not characteristics['color']:
            try:
                color_selectors = [
                    '[data-test-id="product-colors"]',
                    '.color-selector',
                    '.product-colors',
                    '.color-picker'
                ]

                for selector in color_selectors:
                    color_elements = driver.find_elements(By.CSS_SELECTOR, selector)
                    if color_elements:
                        # Поиск активного/выбранного цвета
                        active_selectors = [
                            '.is-active', '.selected',
                            '[aria-selected="true"]', '.active', '.current'
                        ]

                        for active_sel in active_selectors:
                            active = color_elements[0].find_elements(By.CSS_SELECTOR, active_sel)
                            if active:
                                color_text = (
                                    active[0].get_attribute('title') or
                                    active[0].get_attribute('aria-label') or
                                    active[0].text
                                )
                                if color_text and len(color_text) > 1:
                                    characteristics['color'] = color_text.strip()
                                    logger.info(
                                        f"  🎨 Найден цвет из селектора: {characteristics['color']}"
                                    )
                                    break

                        if characteristics['color']:
                            break

            except Exception as e:
                logger.debug(f"Ошибка при поиске цвета в селекторах: {e}")

        # ------------------------------------------------------------
        # СТРАТЕГИЯ 4: Поиск артикула в JavaScript скриптах
        # ------------------------------------------------------------
        if not characteristics['art_set']:
            try:
                scripts = driver.find_elements(
                    By.XPATH,
                    '//script[contains(text(), "sku") or contains(text(), "SKU")]'
                )

                for script in scripts:
                    content = script.get_attribute('innerHTML')
                    if content:
                        # Поиск SKU в различных форматах
                        patterns = [
                            r'"sku"\s*:\s*"(\d+)"',
                            r'"SKU"\s*:\s*"(\d+)"',
                            r'sku\s*=\s*["\'](\d+)["\']',
                            r'productId\s*:\s*(\d+)'
                        ]

                        for pattern in patterns:
                            match = re.search(pattern, content)
                            if match:
                                characteristics['art_set'] = match.group(1)
                                logger.info(
                                    f"  🔢 Найден артикул из скрипта: {characteristics['art_set']}"
                                )
                                break

                        if characteristics['art_set']:
                            break

            except Exception as e:
                logger.debug(f"Ошибка при поиске артикула в скриптах: {e}")

        # ------------------------------------------------------------
        # СТРАТЕГИЯ 5: Поиск в описании товара
        # ------------------------------------------------------------
        if not characteristics['color'] or not characteristics['material']:
            try:
                desc_selectors = [
                    '.rich-content',
                    '.description-block__text',
                    '[data-test-id="product-description"]',
                    '.product-description'
                ]

                for selector in desc_selectors:
                    desc_elem = safe_find_element(driver, [selector], timeout=3)
                    if desc_elem:
                        desc_text = desc_elem.text

                        if desc_text:
                            # Поиск цвета в описании
                            if not characteristics['color']:
                                color_patterns = [
                                    r'цвет[:\s]+([^,.;\n]+)',
                                    r'color[:\s]+([^,.;\n]+)',
                                    r'colour[:\s]+([^,.;\n]+)'
                                ]

                                for pattern in color_patterns:
                                    match = re.search(pattern, desc_text, re.IGNORECASE)
                                    if match:
                                        color_val = match.group(1).strip()
                                        if len(color_val) > 1:
                                            characteristics['color'] = color_val
                                            logger.info(
                                                f"  🎨 Найден цвет в описании: {color_val}"
                                            )
                                            break

                            # Поиск материала в описании
                            if not characteristics['material']:
                                material_patterns = [
                                    r'материал[:\s]+([^,.;\n]+)',
                                    r'material[:\s]+([^,.;\n]+)',
                                    r'состав[:\s]+([^,.;\n]+)',
                                    r'composition[:\s]+([^,.;\n]+)'
                                ]

                                for pattern in material_patterns:
                                    match = re.search(pattern, desc_text, re.IGNORECASE)
                                    if match:
                                        material_val = match.group(1).strip()
                                        if len(material_val) > 1:
                                            characteristics['material'] = material_val
                                            logger.info(
                                                f"  📦 Найден материал в описании: {material_val}"
                                            )
                                            break

                    if characteristics['color'] and characteristics['material']:
                        break

            except Exception as e:
                logger.debug(f"Ошибка при поиске в описании: {e}")

    except Exception as e:
        logger.error(f"❌ Ошибка парсинга характеристик: {e}")

    return characteristics


def parse_rating_from_dom(driver: webdriver.Chrome) -> Optional[float]:
    """
    Поиск рейтинга товара через DOM-элементы.

    Args:
        driver: Экземпляр Selenium драйвера

    Returns:
        Optional[float]: Числовой рейтинг или None
    """
    try:
        rating_selectors = [
            '[data-test-id="product-rating"]',
            '.rating-stars__rating',
            '.product-rating span',
            '.rating-value'
        ]

        for selector in rating_selectors:
            elements = driver.find_elements(By.CSS_SELECTOR, selector)

            for el in elements:
                if not el.is_displayed():
                    continue

                text = el.text.strip()
                if not text:
                    continue

                # Проверка формата: число с десятичной точкой
                if re.match(r'^\d+\.\d+$', text):
                    try:
                        val = float(text)
                        if 0 <= val <= 5:
                            return val
                    except ValueError:
                        continue

    except Exception as e:
        logger.debug(f"Ошибка при парсинге рейтинга из DOM: {e}")

    return None


def parse_product(driver: webdriver.Chrome, sku: str) -> Optional[Dict]:
    """
    Основная функция парсинга карточки товара.

    Выполняет последовательное извлечение всех полей товара:
    - Базовые данные из JSON-LD
    - Характеристики (цвет, материал, артикул)
    - Фотографии и видео
    - Рейтинг и отзывы
    - Наличие Rich Content

    Args:
        driver: Экземпляр Selenium драйвера
        sku: Артикул товара

    Returns:
        Optional[Dict]: Словарь с данными товара или None в случае ошибки
    """
    logger.info(f"📊 Начало парсинга SKU: {sku}")

    # Инициализация структуры данных товара
    product = {
        'sku': sku,
        'title': '',
        'price': None,
        'rating': None,
        'reviews_total': 0,
        'cover_image': '',
        'photos_seller': 0,
        'videos_seller': 0,
        'color': '',
        'material': '',
        'art_set': '',
        'has_rich_content': False
    }

    try:
        # Ожидание загрузки страницы
        WebDriverWait(driver, 15).until(
            EC.presence_of_element_located((By.CSS_SELECTOR, 'body'))
        )
        time.sleep(random.uniform(2, 4))

        html_content = driver.page_source

        # Проверка на блокировку страницы
        block_indicators = ['captcha', 'access denied', 'похоже, нет соединения']
        if any(indicator in html_content.lower() for indicator in block_indicators):
            logger.warning(f"⚠️ Страница SKU {sku} заблокирована")
            return None

        # ================================================================
        # 1. Парсинг из JSON-LD
        # ================================================================
        json_ld = extract_json_ld(html_content)

        if json_ld:
            product = parse_from_json_ld(json_ld, product)
            title_preview = product['title'][:40] + '...' if len(product['title']) > 40 else product['title']
            logger.info(f"✅ Данные из JSON-LD: '{title_preview}'")
        else:
            logger.warning("⚠️ JSON-LD не найден, используется DOM-парсинг")

        # ================================================================
        # 2. Парсинг характеристик (цвет, материал, артикул)
        # ================================================================
        characteristics = parse_characteristics_advanced(driver)
        product.update(characteristics)

        # Если артикул не найден, используем SKU из URL
        if not product['art_set']:
            product['art_set'] = sku
            logger.info(f"  🔢 Артикул из URL: {sku}")

        # ================================================================
        # 3. Парсинг фотографий
        # ================================================================
        if product['photos_seller'] == 0:
            try:
                # Прокрутка для загрузки галереи
                driver.execute_script("window.scrollTo(0, 200);")
                time.sleep(1)

                gallery_selectors = [
                    '.GalleryWidget__thumbs img',
                    '.gallery__thumb img',
                    'img[data-test-id="gallery-image"]',
                    '.web-pdp-gallery-thumbs img'
                ]

                gallery_images = []
                for sel in gallery_selectors:
                    imgs = driver.find_elements(By.CSS_SELECTOR, sel)
                    if imgs:
                        gallery_images = imgs
                        break

                photos = []
                for img in gallery_images:
                    src = img.get_attribute('src') or img.get_attribute('data-src')
                    if src and 'ozon' in src:
                        # Удаление параметров размера
                        src = re.sub(r'\?.*$', '', src)
                        if 'thumb' not in src and 'icon' not in src:
                            photos.append(src)

                # Удаление дубликатов
                photos = list(set(photos))
                product['photos_seller'] = len(photos)

                if photos and not product['cover_image']:
                    product['cover_image'] = photos[0]

            except Exception as e:
                logger.debug(f"Не удалось посчитать фото: {e}")

        # ================================================================
        # 4. Парсинг видео
        # ================================================================
        try:
            video_selectors = [
                'video',
                '.gallery__video-thumb',
                '[data-test-id="video"]'
            ]

            videos = []
            for sel in video_selectors:
                vids = driver.find_elements(By.CSS_SELECTOR, sel)
                if vids:
                    videos = vids
                    break

            product['videos_seller'] = len(videos)

        except Exception as e:
            logger.debug(f"Не удалось посчитать видео: {e}")

        # ================================================================
        # 5. Проверка Rich Content в описании
        # ================================================================
        if not product['has_rich_content']:
            try:
                driver.execute_script("window.scrollTo(0, 800);")
                time.sleep(1)

                desc_selectors = [
                    '.rich-content',
                    '.description-block__text',
                    '[data-test-id="product-description"]'
                ]

                desc_elem = safe_find_element(driver, desc_selectors)

                if desc_elem:
                    html_desc = desc_elem.get_attribute('innerHTML')

                    if html_desc:
                        has_img = '<img' in html_desc.lower()
                        has_table = '<table' in html_desc.lower()
                        has_list = '<ul' in html_desc.lower() or '<ol' in html_desc.lower()

                        product['has_rich_content'] = has_img or has_table or has_list

                        if product['has_rich_content']:
                            logger.info("✅ Найден Rich Content в описании")

            except Exception as e:
                logger.debug(f"Не удалось проверить Rich Content: {e}")

        # ================================================================
        # 6. Дополнительный парсинг для отсутствующих полей
        # ================================================================

        # Название товара
        if not product['title']:
            title_selectors = [
                'h1[data-test-id="product-title"]',
                '.product-title',
                '.item-title'
            ]
            title_elem = safe_find_element(driver, title_selectors)
            if title_elem:
                product['title'] = title_elem.text.strip()

        # Цена
        if product['price'] is None:
            price_selectors = [
                '.price-block__final-price',
                '[data-test-id="product-price"]',
                '.product-price'
            ]
            price_elem = safe_find_element(driver, price_selectors)

            if price_elem:
                price_text = re.sub(r'[^\d.,]', '', price_elem.text.strip())
                if price_text:
                    try:
                        product['price'] = float(price_text.replace(',', '.'))
                    except ValueError:
                        pass

        # Рейтинг
        if product['rating'] is None:
            rating = parse_rating_from_dom(driver)
            if rating is not None:
                product['rating'] = rating
                logger.info(f"✅ Найден рейтинг: {product['rating']}")

        # Количество отзывов
        if product['reviews_total'] == 0:
            reviews_selectors = [
                '.review-block__count',
                '[data-test-id="reviews-count"]',
                '.product-reviews__count'
            ]
            reviews_elem = safe_find_element(driver, reviews_selectors)

            if reviews_elem:
                numbers = re.findall(r'\d+', reviews_elem.text.strip())
                if numbers:
                    product['reviews_total'] = int(numbers[0])
                    logger.info(f"✅ Найдено отзывов: {product['reviews_total']}")

        # Обложка товара
        if not product['cover_image']:
            cover_selectors = [
                '.gallery__main img',
                '[data-test-id="product-image"]',
                '.product-image img'
            ]
            cover_elem = safe_find_element(driver, cover_selectors)

            if cover_elem:
                src = cover_elem.get_attribute('src') or cover_elem.get_attribute('data-src')
                if src and 'ozon' in src:
                    src = re.sub(r'\?.*$', '', src)
                    product['cover_image'] = src

        # ================================================================
        # 7. Вывод итоговых характеристик
        # ================================================================
        logger.info("📋 Итоговые характеристики:")
        logger.info(f"  🎨 Цвет: {product['color'] or 'не указан'}")
        logger.info(f"  📦 Материал: {product['material'] or 'не указан'}")
        logger.info(f"  🔢 Артикул: {product['art_set'] or 'не найден'}")

        logger.info(f"🏁 Парсинг SKU {sku} завершен")
        return product

    except TimeoutException as e:
        logger.error(f"❌ Таймаут при загрузке страницы SKU {sku}: {e}")
        return None
    except Exception as e:
        logger.error(f"❌ Ошибка при парсинге SKU {sku}: {e}", exc_info=True)
        return None


# ============================================================================
# СОХРАНЕНИЕ РЕЗУЛЬТАТОВ
# ============================================================================

def save_to_csv(results: List[Dict], filename: str) -> bool:
    """
    Сохранение результатов парсинга в CSV-файл.

    Args:
        results: Список словарей с данными товаров
        filename: Имя выходного файла

    Returns:
        bool: True в случае успеха
    """
    if not results:
        logger.warning("Нет данных для сохранения")
        return False

    fieldnames = [
        'sku', 'title', 'price', 'rating', 'reviews_total',
        'cover_image', 'photos_seller', 'videos_seller',
        'color', 'material', 'art_set', 'has_rich_content'
    ]

    try:
        # Если файл занят, создаем новый с таймштампом
        if os.path.exists(filename):
            try:
                with open(filename, 'r', encoding='utf-8-sig') as f:
                    pass
            except PermissionError:
                timestamp = time.strftime("%Y%m%d_%H%M%S")
                filename = f"ozon_products_{timestamp}.csv"
                logger.warning(f"⚠️ Файл занят. Сохраняем как {filename}")

        # Запись в CSV
        with open(filename, 'w', newline='', encoding='utf-8-sig') as f:
            writer = csv.DictWriter(
                f,
                fieldnames=fieldnames,
                delimiter=';',
                extrasaction='ignore'
            )
            writer.writeheader()
            writer.writerows(results)

        logger.info(f"💾 Сохранено {len(results)} товаров в '{filename}'")
        return True

    except Exception as e:
        logger.error(f"❌ Ошибка сохранения CSV: {e}")
        return False


def save_to_json(results: List[Dict], filename: str) -> bool:
    """
    Сохранение результатов парсинга в JSON-файл (резервное сохранение).

    Args:
        results: Список словарей с данными товаров
        filename: Имя выходного файла

    Returns:
        bool: True в случае успеха
    """
    try:
        with open(filename, 'w', encoding='utf-8') as f:
            json.dump(results, f, ensure_ascii=False, indent=2)

        logger.info(f"💾 Резервное сохранение в '{filename}'")
        return True

    except Exception as e:
        logger.error(f"❌ Ошибка сохранения JSON: {e}")
        return False


def print_sample_data(results: List[Dict]) -> None:
    """
    Вывод примера спарсенных данных в консоль.

    Args:
        results: Список словарей с данными товаров
    """
    if not results:
        return

    logger.info("\n📋 Пример спарсенных данных:")
    for key, value in results[0].items():
        logger.info(f"  {key}: {value}")


# ============================================================================
# ОСНОВНАЯ ФУНКЦИЯ
# ============================================================================

def main():
    """
    Основная функция парсера.

    Выполняет:
    1. Загрузку cookies
    2. Инициализацию сессии
    3. Парсинг товаров по списку SKU
    4. Сохранение результатов
    """
    # Список SKU для парсинга
    SKUS = ['2359066702', '2829800382']

    # Логирование начала работы
    logger.info("=" * 70)
    logger.info("🚀 ЗАПУСК ПАРСЕРА OZON")
    logger.info(f"📋 Количество товаров: {len(SKUS)}")
    logger.info("=" * 70)

    # Загрузка cookies
    cookies = load_cookies(COOKIES_FILE)

    if not cookies:
        logger.error("❌ Нет валидных cookies. Запустите get_cookies.py")
        return

    results = []
    driver = None

    try:
        # Настройка Chrome драйвера
        driver = setup_chrome_driver()
        logger.debug("Chrome драйвер создан")

        # Инициализация сессии с cookies
        if not initialize_session(driver, cookies):
            raise Exception("Не удалось инициализировать сессию")

        # Парсинг каждого SKU
        for idx, sku in enumerate(SKUS, 1):
            logger.info(f"\n--- [{idx}/{len(SKUS)}] Обработка товара SKU: {sku} ---")

            url = f'https://www.ozon.ru/product/{sku}/'

            try:
                logger.info(f"🌐 Загрузка страницы: {url}")
                driver.get(url)
                time.sleep(random.uniform(3, 5))

                # Парсинг товара
                product_data = parse_product(driver, sku)

                if product_data:
                    results.append(product_data)
                    logger.info(f"✅ Товар {sku} успешно спарсен")
                else:
                    logger.warning(f"⚠️ Товар {sku} пропущен")

            except TimeoutException as e:
                logger.error(f"❌ Таймаут при загрузке SKU {sku}: {e}")
            except Exception as e:
                logger.error(f"❌ Ошибка при обработке товара {sku}: {e}")

            # Пауза между запросами
            if idx < len(SKUS):
                delay = random.uniform(8, 15)
                logger.info(f"⏳ Пауза {delay:.1f} сек...")
                time.sleep(delay)

    except Exception as e:
        logger.error(f"❌ Критическая ошибка: {e}", exc_info=True)

    finally:
        # Закрытие браузера
        if driver:
            logger.info("🔄 Закрытие браузера...")
            driver.quit()

    # Сохранение результатов
    if results:
        logger.info(f"\n📊 Итоги парсинга: {len(results)} товаров")

        # Сохранение в CSV
        if save_to_csv(results, OUTPUT_CSV):
            logger.info("✅ CSV сохранен успешно")
        else:
            # Если CSV не удался, сохраняем в JSON
            save_to_json(results, OUTPUT_JSON)

        # Вывод примера данных
        print_sample_data(results)

    else:
        logger.error("❌ Не удалось спарсить ни один товар")

    logger.info("=" * 70)
    logger.info("🏁 РАБОТА ПАРСЕРА ЗАВЕРШЕНА")
    logger.info("=" * 70)


# ============================================================================
# ТОЧКА ВХОДА
# ============================================================================

if __name__ == '__main__':
    try:
        main()
    except KeyboardInterrupt:
        logger.info("\n⏹ Программа остановлена пользователем")
    except Exception as e:
        logger.error(f"❌ Непредвиденная ошибка: {e}", exc_info=True)