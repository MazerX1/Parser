# parse_ozon.py
"""
Парсер карточек товаров Ozon с использованием Selenium и cookies.

Основные функции:
1. Загрузка cookies из файла для авторизации
2. Парсинг карточек товаров по списку SKU
3. Извлечение всех необходимых полей согласно ТЗ
4. Сохранение результатов в CSV и JSON форматах
"""

import os
import re
import csv
import json
import time
import random
import logging
from typing import List, Dict, Optional, Any, Tuple
from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.chrome.options import Options
from selenium.common.exceptions import TimeoutException, NoSuchElementException

# ==================== НАСТРОЙКИ ====================

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

# Константы
COOKIES_FILE = 'ozon_data_cookies.json'
OUTPUT_CSV = 'ozon_products.csv'
OUTPUT_JSON = 'ozon_products.json'
USER_AGENT = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36'

# Селекторы для поиска элементов
SELECTORS = {
    'title': [
        'h1[data-test-id="product-title"]',
        '.product-title',
        '.item-title'
    ],
    'price': [
        '.price-block__final-price',
        '[data-test-id="product-price"]',
        '.product-price'
    ],
    'rating': [
        '.rating-stars__rating',
        '[data-test-id="product-rating"]',
        '.product-rating span'
    ],
    'reviews': [
        '.review-block__count',
        '[data-test-id="reviews-count"]',
        '.product-reviews__count'
    ],
    'cover': [
        '.gallery__main img',
        '[data-test-id="product-image"]',
        '.product-image img'
    ],
    'gallery': [
        '.GalleryWidget__thumbs img',
        '.gallery__thumb img',
        'img[data-test-id="gallery-image"]'
    ],
    'description': [
        '.rich-content',
        '.description-block__text',
        '[data-test-id="product-description"]'
    ],
    'characteristics': [
        '.characteristics-block__item',
        '[data-test-id="product-characteristics"] > div',
        '.product-attributes li'
    ]
}


# ==================== РАБОТА С COOKIES ====================

def load_cookies(path: str) -> List[Dict]:
    """
    Загрузка и форматирование cookies из JSON файла.

    Args:
        path: Путь к файлу с cookies

    Returns:
        List[Dict]: Список отформатированных cookies для Selenium
    """
    if not os.path.exists(path):
        logger.error(f"❌ Файл cookies не найден: {path}")
        logger.info("📝 Сначала запустите get_cookies.py для получения cookies")
        return []

    try:
        with open(path, 'r', encoding='utf-8') as f:
            raw_data = json.load(f)

        formatted_cookies = []

        # Поддержка разных форматов
        if isinstance(raw_data, list):
            formatted_cookies = raw_data
        elif isinstance(raw_data, dict):
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
        logger.error(f"❌ Ошибка парсинга JSON: {e}")
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
    logger.debug("🔄 Настройка Chrome драйвера...")

    options = Options()

    # Основные опции для обхода антибот-систем
    options.add_argument('--disable-blink-features=AutomationControlled')
    options.add_experimental_option("excludeSwitches", ["enable-automation"])
    options.add_experimental_option('useAutomationExtension', False)

    # Системные опции
    options.add_argument('--disable-gpu')
    options.add_argument('--no-sandbox')
    options.add_argument('--disable-dev-shm-usage')
    options.add_argument('--start-maximized')
    options.add_argument('--disable-extensions')
    options.add_argument('--disable-popup-blocking')
    options.add_argument('--disable-infobars')
    options.add_argument('--disable-notifications')
    options.add_argument(f'user-agent={USER_AGENT}')

    driver = webdriver.Chrome(options=options)

    # Маскировка автоматизации
    driver.execute_script(
        "Object.defineProperty(navigator, 'webdriver', {get: () => undefined})"
    )

    logger.debug("✅ Chrome драйвер настроен")
    return driver


def add_cookies_to_driver(driver: webdriver.Chrome, cookies: List[Dict]) -> None:
    """
    Добавление cookies в сессию драйвера.

    Args:
        driver: Экземпляр WebDriver
        cookies: Список cookies для добавления
    """
    success_count = 0

    for cookie in cookies:
        try:
            cookie_data = {
                'name': cookie['name'],
                'value': cookie['value'],
                'domain': cookie.get('domain', '.ozon.ru'),
                'path': cookie.get('path', '/'),
                'secure': cookie.get('secure', True)
            }
            driver.add_cookie(cookie_data)
            success_count += 1
        except Exception as e:
            logger.debug(f"Не удалось добавить cookie {cookie.get('name')}: {e}")

    logger.info(f"✅ Добавлено {success_count} cookies из {len(cookies)}")


def initialize_session(driver: webdriver.Chrome, cookies: List[Dict]) -> bool:
    """
    Инициализация сессии с cookies.

    Args:
        driver: Экземпляр WebDriver
        cookies: Список cookies

    Returns:
        bool: Успешность инициализации
    """
    try:
        logger.info("🌐 Открытие главной страницы...")
        driver.get('https://www.ozon.ru/')
        time.sleep(2)

        add_cookies_to_driver(driver, cookies)

        logger.info("🔄 Обновление страницы...")
        driver.refresh()
        time.sleep(3)

        logger.info("✅ Сессия инициализирована")
        return True

    except Exception as e:
        logger.error(f"❌ Ошибка инициализации сессии: {e}")
        return False


# ==================== ПАРСИНГ ДАННЫХ ====================

def extract_json_ld(html: str) -> Optional[Dict[str, Any]]:
    """
    Извлечение структурированных данных JSON-LD из HTML.

    Args:
        html: HTML-код страницы

    Returns:
        Optional[Dict]: Данные товара в формате JSON или None
    """
    try:
        # Поиск скрипта с JSON-LD
        pattern = r'<script[^>]*type=["\']application/ld\+json["\'][^>]*>(.*?)</script>'
        match = re.search(pattern, html, re.DOTALL)

        if not match:
            return None

        # Очистка и парсинг JSON
        clean_json = match.group(1).replace('\\"', '"')
        data = json.loads(clean_json)

        # Поиск объекта Product
        if isinstance(data, dict) and data.get('@type') == 'Product':
            return data
        if isinstance(data, list):
            for item in data:
                if isinstance(item, dict) and item.get('@type') == 'Product':
                    return item

    except json.JSONDecodeError as e:
        logger.debug(f"Ошибка парсинга JSON-LD: {e}")
    except Exception as e:
        logger.debug(f"Ошибка извлечения JSON-LD: {e}")

    return None


def parse_from_json_ld(json_ld: Dict[str, Any], product: Dict) -> Dict:
    """
    Парсинг данных из JSON-LD.

    Args:
        json_ld: Данные JSON-LD
        product: Словарь с данными товара

    Returns:
        Dict: Обновленный словарь с данными
    """
    # Название
    product['title'] = json_ld.get('name', '')

    # Изображение
    product['cover_image'] = json_ld.get('image', '')

    # Цена
    offers = json_ld.get('offers', {})
    if isinstance(offers, dict):
        price_val = offers.get('price')
        if price_val is not None:
            try:
                product['price'] = float(str(price_val).replace(',', '.'))
            except (ValueError, TypeError):
                pass

    # Рейтинг и отзывы
    agg_rating = json_ld.get('aggregateRating', {})
    if isinstance(agg_rating, dict):
        try:
            rating_val = agg_rating.get('ratingValue')
            if rating_val is not None:
                if isinstance(rating_val, (int, float)):
                    product['rating'] = float(rating_val)
                elif isinstance(rating_val, str):
                    rating_clean = re.sub(r'[^\d.]', '', rating_val.replace(',', '.'))
                    if rating_clean:
                        product['rating'] = float(rating_clean)

            review_count = agg_rating.get('reviewCount')
            if review_count is not None:
                try:
                    product['reviews_total'] = int(review_count)
                except (ValueError, TypeError):
                    pass
        except (ValueError, TypeError) as e:
            logger.debug(f"Ошибка парсинга рейтинга: {e}")

    # Проверка Rich Content в описании
    description = str(json_ld.get('description', ''))
    if '<img' in description or '<table' in description or '<ul>' in description:
        product['has_rich_content'] = True

    return product


def safe_find_element(driver: webdriver.Chrome, selectors: List[str], timeout: int = 5):
    """
    Безопасный поиск элемента по списку селекторов.

    Args:
        driver: Экземпляр WebDriver
        selectors: Список CSS-селекторов
        timeout: Время ожидания в секундах

    Returns:
        WebElement или None
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


def safe_find_elements(driver: webdriver.Chrome, selectors: List[str], timeout: int = 3):
    """
    Безопасный поиск элементов по списку селекторов.

    Args:
        driver: Экземпляр WebDriver
        selectors: Список CSS-селекторов
        timeout: Время ожидания в секундах

    Returns:
        List[WebElement] или пустой список
    """
    for selector in selectors:
        try:
            WebDriverWait(driver, timeout).until(
                EC.presence_of_element_located((By.CSS_SELECTOR, selector))
            )
            elements = driver.find_elements(By.CSS_SELECTOR, selector)
            if elements:
                return elements
        except TimeoutException:
            continue
        except Exception:
            continue
    return []


def parse_characteristics(driver: webdriver.Chrome) -> Dict[str, str]:
    """
    Парсинг характеристик товара (цвет, материал, артикул).

    Args:
        driver: Экземпляр WebDriver

    Returns:
        Dict: Словарь с характеристиками
    """
    characteristics = {
        'color': '',
        'material': '',
        'art_set': ''
    }

    try:
        # Прокрутка для загрузки характеристик
        driver.execute_script("window.scrollTo(0, 400);")
        time.sleep(1)

        # Поиск блока с характеристиками
        for selector in SELECTORS['characteristics']:
            try:
                elements = driver.find_elements(By.CSS_SELECTOR, selector)
                if not elements:
                    continue

                for element in elements:
                    try:
                        text = element.text.strip()
                        if not text:
                            continue

                        # Разбор текста на ключ и значение
                        key, val = None, None
                        if ':' in text:
                            key, val = text.split(':', 1)
                        elif '—' in text:
                            key, val = text.split('—', 1)
                        elif '\n' in text:
                            parts = text.split('\n', 1)
                            if len(parts) == 2:
                                key, val = parts
                        else:
                            continue

                        if not key or not val:
                            continue

                        key_lower = key.strip().lower()
                        val_clean = val.strip()

                        # Определение типа характеристики
                        if any(k in key_lower for k in ['цвет', 'color']):
                            characteristics['color'] = val_clean
                            logger.debug(f"  Найден цвет: {val_clean}")
                        elif any(k in key_lower for k in ['материал', 'material', 'состав']):
                            characteristics['material'] = val_clean
                            logger.debug(f"  Найден материал: {val_clean}")
                        elif any(k in key_lower for k in ['артикул', 'part number', 'model', 'комплектация']):
                            characteristics['art_set'] = val_clean
                            logger.debug(f"  Найден артикул: {val_clean}")

                    except Exception:
                        continue

                # Если найдены все характеристики - прерываем поиск
                if all(characteristics.values()):
                    break

            except Exception:
                continue

    except Exception as e:
        logger.debug(f"Ошибка парсинга характеристик: {e}")

    return characteristics


def parse_product(driver: webdriver.Chrome, sku: str) -> Optional[Dict]:
    """
    Основная функция парсинга карточки товара.

    Args:
        driver: Экземпляр WebDriver
        sku: Артикул товара

    Returns:
        Optional[Dict]: Данные товара или None
    """
    logger.info(f"📊 Начало парсинга SKU: {sku}")

    # Инициализация структуры данных
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

        # Проверка на блокировку
        html_lower = html_content.lower()
        if any(block in html_lower for block in ['captcha', 'access denied', 'похоже, нет соединения']):
            logger.warning(f"⚠️ Страница SKU {sku} заблокирована")
            return None

        # --- 1. Парсинг из JSON-LD ---
        json_ld = extract_json_ld(html_content)
        if json_ld:
            product = parse_from_json_ld(json_ld, product)
            logger.info(f"✅ Данные из JSON-LD: '{product['title'][:40]}...'")
        else:
            logger.warning("⚠️ JSON-LD не найден, используется DOM-парсинг")

        # --- 2. Парсинг характеристик ---
        characteristics = parse_characteristics(driver)
        product['color'] = characteristics['color']
        product['material'] = characteristics['material']
        product['art_set'] = characteristics['art_set']

        # --- 3. Парсинг фото ---
        try:
            driver.execute_script("window.scrollTo(0, 200);")
            time.sleep(1)

            gallery_images = safe_find_elements(driver, SELECTORS['gallery'])
            photos = []

            for img in gallery_images:
                src = img.get_attribute('src') or img.get_attribute('data-src')
                if src and 'ozone' in src:
                    if 'icon' not in src and 'thumb' not in src and 'logo' not in src:
                        photos.append(src)

            photos = list(set(photos))
            product['photos_seller'] = len(photos)

            if photos and not product['cover_image']:
                product['cover_image'] = photos[0]

        except Exception as e:
            logger.debug(f"Не удалось посчитать фото: {e}")

        # --- 4. Парсинг видео ---
        try:
            video_selectors = ['video', '.gallery__video-thumb', '[data-test-id="video"]']
            videos = safe_find_elements(driver, video_selectors)
            product['videos_seller'] = len(videos)
        except Exception as e:
            logger.debug(f"Не удалось посчитать видео: {e}")

        # --- 5. Проверка Rich Content ---
        if not product['has_rich_content']:
            try:
                driver.execute_script("window.scrollTo(0, 800);")
                time.sleep(1)

                desc_elem = safe_find_element(driver, SELECTORS['description'])
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

        # --- 6. Дополнительный парсинг для отсутствующих полей ---
        if not product['title']:
            title_elem = safe_find_element(driver, SELECTORS['title'])
            if title_elem:
                product['title'] = title_elem.text.strip()

        if product['price'] is None:
            price_elem = safe_find_element(driver, SELECTORS['price'])
            if price_elem:
                price_text = re.sub(r'[^\d.,]', '', price_elem.text.strip())
                if price_text:
                    product['price'] = float(price_text.replace(',', '.'))

        if product['rating'] is None:
            rating_elem = safe_find_element(driver, SELECTORS['rating'])
            if rating_elem:
                rating_text = rating_elem.text.strip()
                rating_clean = re.sub(r'[^\d.]', '', rating_text.replace(',', '.'))
                if rating_clean:
                    product['rating'] = float(rating_clean)
                    logger.info(f"✅ Найден рейтинг: {product['rating']}")

        if product['reviews_total'] == 0:
            reviews_elem = safe_find_element(driver, SELECTORS['reviews'])
            if reviews_elem:
                numbers = re.findall(r'\d+', reviews_elem.text.strip())
                if numbers:
                    product['reviews_total'] = int(numbers[0])
                    logger.info(f"✅ Найдено отзывов: {product['reviews_total']}")

        if not product['cover_image']:
            cover_elem = safe_find_element(driver, SELECTORS['cover'])
            if cover_elem:
                src = cover_elem.get_attribute('src') or cover_elem.get_attribute('data-src')
                if src and 'ozone' in src:
                    product['cover_image'] = src

        logger.info(f"🏁 Парсинг SKU {sku} завершен")
        return product

    except Exception as e:
        logger.error(f"❌ Ошибка при парсинге SKU {sku}: {e}", exc_info=True)
        return None


# ==================== СОХРАНЕНИЕ РЕЗУЛЬТАТОВ ====================

def save_to_csv(results: List[Dict], filename: str) -> bool:
    """
    Сохранение результатов в CSV файл.

    Args:
        results: Список с данными товаров
        filename: Имя файла

    Returns:
        bool: Успешность сохранения
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
        # Проверка доступности файла
        if os.path.exists(filename):
            try:
                with open(filename, 'r', encoding='utf-8-sig') as f:
                    pass
            except PermissionError:
                timestamp = time.strftime("%Y%m%d_%H%M%S")
                filename = f"ozon_products_{timestamp}.csv"
                logger.warning(f"⚠️ Файл занят. Сохраняем как {filename}")

        with open(filename, 'w', newline='', encoding='utf-8-sig') as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames, delimiter=';', extrasaction='ignore')
            writer.writeheader()
            writer.writerows(results)

        logger.info(f"💾 Сохранено {len(results)} товаров в '{filename}'")
        return True

    except Exception as e:
        logger.error(f"❌ Ошибка сохранения CSV: {e}")
        return False


def save_to_json(results: List[Dict], filename: str) -> bool:
    """
    Сохранение результатов в JSON файл.

    Args:
        results: Список с данными товаров
        filename: Имя файла

    Returns:
        bool: Успешность сохранения
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
    Вывод примера спарсенных данных.

    Args:
        results: Список с данными товаров
    """
    if not results:
        return

    logger.info("\n📋 Пример спарсенных данных:")
    for key, value in results[0].items():
        logger.info(f"  {key}: {value}")


# ==================== ОСНОВНАЯ ЛОГИКА ====================

def main():
    """
    Основная функция парсера.

    Этапы работы:
    1. Загрузка cookies
    2. Инициализация браузера и сессии
    3. Парсинг товаров по списку SKU
    4. Сохранение результатов
    """
    # Список SKU для парсинга
    SKUS = ['2359066702', '2829800382']

    logger.info("=" * 70)
    logger.info("🚀 ЗАПУСК ПАРСЕРА OZON")
    logger.info("📋 Количество товаров: {len(SKUS)}")
    logger.info("=" * 70)

    # 1. Загрузка cookies
    cookies = load_cookies(COOKIES_FILE)
    if not cookies:
        logger.error("❌ Нет валидных cookies. Запустите get_cookies.py")
        return

    results = []
    driver = None

    try:
        # 2. Инициализация сессии
        driver = setup_chrome_driver()

        if not initialize_session(driver, cookies):
            raise Exception("Не удалось инициализировать сессию")

        # 3. Парсинг товаров
        for idx, sku in enumerate(SKUS, 1):
            logger.info(f"\n--- [{idx}/{len(SKUS)}] Обработка товара SKU: {sku} ---")

            url = f'https://www.ozon.ru/product/{sku}/'

            try:
                logger.info(f"🌐 Загрузка страницы: {url}")
                driver.get(url)
                time.sleep(random.uniform(3, 5))

                product_data = parse_product(driver, sku)

                if product_data:
                    results.append(product_data)
                    logger.info(f"✅ Товар {sku} успешно спарсен")
                else:
                    logger.warning(f"⚠️ Товар {sku} пропущен")

            except Exception as e:
                logger.error(f"❌ Ошибка при обработке товара {sku}: {e}")

            # Пауза между запросами
            if idx < len(SKUS):
                delay = random.uniform(5, 10)
                logger.info(f"⏳ Пауза {delay:.1f} сек...")
                time.sleep(delay)

    except Exception as e:
        logger.error(f"❌ Критическая ошибка: {e}", exc_info=True)

    finally:
        if driver:
            logger.info("🔄 Закрытие браузера...")
            driver.quit()

    # 4. Сохранение результатов
    if results:
        logger.info(f"\n📊 Итоги парсинга: {len(results)} товаров")

        # Сохранение в CSV
        if save_to_csv(results, OUTPUT_CSV):
            logger.info("✅ CSV сохранен успешно")
        else:
            # Резервное сохранение в JSON
            save_to_json(results, OUTPUT_JSON)

        # Вывод примера данных
        print_sample_data(results)

    else:
        logger.error("❌ Не удалось спарсить ни один товар")

    logger.info("=" * 70)
    logger.info("🏁 РАБОТА ПАРСЕРА ЗАВЕРШЕНА")
    logger.info("=" * 70)


if __name__ == '__main__':
    try:
        main()
    except KeyboardInterrupt:
        logger.info("\n🛑 Программа остановлена пользователем")
    except Exception as e:
        logger.error(f"❌ Непредвиденная ошибка: {e}", exc_info=True)