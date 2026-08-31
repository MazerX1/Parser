"""
Скрипт для парсинга данных о товарах с Ozon по списку SKU.
Использует сохраненные cookies для авторизованных запросов.
Результаты сохраняются в CSV файл.
"""

import os
import re
import csv
import json
import time
import logging
from typing import List, Dict, Optional
from pathlib import Path

import requests
from bs4 import BeautifulSoup
import pandas as pd

# Настройка логирования
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


class OzonParser:
    """Класс для парсинга данных о товарах с Ozon."""

    def __init__(self):
        self.session = requests.Session()
        self.cookies_file = 'ozon_cookies.json'
        self.output_file = 'ozon_products.csv'

        # Заголовки для имитации браузера
        self.headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
            'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8',
            'Accept-Language': 'ru-RU,ru;q=0.9,en-US;q=0.8,en;q=0.7',
        }
        self.session.headers.update(self.headers)

    def load_cookies(self) -> bool:
        """Загрузка cookies из файла."""
        try:
            if not Path(self.cookies_file).exists():
                logger.error(f"Файл cookies {self.cookies_file} не найден. Сначала выполните get_cookies.py")
                return False

            with open(self.cookies_file, 'r', encoding='utf-8') as f:
                cookies_dict = json.load(f)

            for name, value in cookies_dict.items():
                self.session.cookies.set(name, value)

            logger.info(f"Cookies загружены из файла {self.cookies_file}")
            return True

        except Exception as e:
            logger.error(f"Ошибка при загрузке cookies: {e}")
            return False

    def parse_product_page(self, sku: str) -> Optional[Dict]:
        """Парсинг страницы товара по SKU."""
        url = f'https://www.ozon.ru/product/{sku}/'

        try:
            logger.info(f"Парсинг товара с SKU: {sku}")

            response = self.session.get(url, timeout=10)

            if response.status_code != 200:
                logger.error(f"Ошибка загрузки страницы {sku}: {response.status_code}")
                return None

            soup = BeautifulSoup(response.text, 'html.parser')

            # Извлекаем данные из JSON-LD или встроенных скриптов
            product_data = self._extract_product_data(soup, response.text)

            if not product_data:
                logger.warning(f"Не удалось извлечь данные для SKU: {sku}")
                return None

            # Формируем результат
            result = {
                'sku': sku,
                'title': product_data.get('title', ''),
                'price': self._extract_price(product_data),
                'rating': product_data.get('rating', 0),
                'reviews_total': product_data.get('reviews_count', 0),
                'cover_image': product_data.get('main_image', ''),
                'photos_seller': product_data.get('photos_count', 0),
                'videos_seller': product_data.get('videos_count', 0),
                'color': product_data.get('color', ''),
                'material': product_data.get('material', ''),
                'art_set': product_data.get('manufacturer_sku', ''),
                'has_rich_content': self._check_rich_content(soup)
            }

            logger.info(f"Успешно распарсен товар {sku}: {result['title'][:50]}...")
            return result

        except Exception as e:
            logger.error(f"Ошибка при парсинге SKU {sku}: {e}")
            return None

    def _extract_product_data(self, soup: BeautifulSoup, html_text: str) -> Optional[Dict]:
        """Извлечение данных о товаре из HTML."""
        product_data = {}

        # Попытка извлечь данные из JSON-LD
        json_ld_scripts = soup.find_all('script', type='application/ld+json')
        for script in json_ld_scripts:
            try:
                data = json.loads(script.string)
                if isinstance(data, dict) and data.get('@type') == 'Product':
                    product_data.update(self._parse_json_ld(data))
            except (json.JSONDecodeError, AttributeError):
                continue

        # Если не нашли в JSON-LD, ищем в других местах
        if not product_data.get('title'):
            # Ищем название в meta тегах
            title_tag = soup.find('meta', property='og:title')
            if title_tag:
                product_data['title'] = title_tag.get('content', '')

        # Извлекаем цену
        if not product_data.get('price'):
            price_element = soup.find('span', {'data-widget': 'webPrice'})
            if price_element:
                price_text = price_element.get_text(strip=True)
                price_value = re.search(r'[\d\s]+', price_text)
                if price_value:
                    product_data['price'] = int(price_value.group().replace(' ', ''))

        # Извлекаем рейтинг и отзывы
        rating_element = soup.find('div', class_='j7_e1t')
        if rating_element:
            rating_text = rating_element.get_text(strip=True)
            rating_match = re.search(r'(\d+\.\d+)', rating_text)
            if rating_match:
                product_data['rating'] = float(rating_match.group(1))

            reviews_match = re.search(r'(\d+)\s*отзыв', rating_text)
            if reviews_match:
                product_data['reviews_count'] = int(reviews_match.group(1))

        # Извлекаем главное изображение
        if not product_data.get('main_image'):
            image_tag = soup.find('meta', property='og:image')
            if image_tag:
                product_data['main_image'] = image_tag.get('content', '')

        # Извлекаем количество фото и видео
        photos_element = soup.find('div', class_='g8h2i')
        if photos_element:
            photos_text = photos_element.get_text(strip=True)
            photos_match = re.search(r'(\d+)\s*фото', photos_text)
            if photos_match:
                product_data['photos_count'] = int(photos_match.group(1))

            videos_match = re.search(r'(\d+)\s*видео', photos_text)
            if videos_match:
                product_data['videos_count'] = int(videos_match.group(1))

        # Извлекаем характеристики (цвет, материал)
        characteristics = self._extract_characteristics(soup)
        product_data.update(characteristics)

        return product_data if product_data.get('title') else None

    def _parse_json_ld(self, data: dict) -> Dict:
        """Парсинг данных из JSON-LD формата."""
        result = {}

        if 'name' in data:
            result['title'] = data['name']

        if 'image' in data:
            if isinstance(data['image'], list):
                result['main_image'] = data['image'][0] if data['image'] else ''
            else:
                result['main_image'] = data['image']

        if 'aggregateRating' in data:
            rating_data = data['aggregateRating']
            result['rating'] = float(rating_data.get('ratingValue', 0))
            result['reviews_count'] = int(rating_data.get('reviewCount', 0))

        if 'offers' in data:
            offers = data['offers']
            if isinstance(offers, list):
                offers = offers[0] if offers else {}

            if 'price' in offers:
                result['price'] = float(offers['price'])

        return result

    def _extract_price(self, product_data: Dict) -> Optional[float]:
        """Извлечение и нормализация цены."""
        price = product_data.get('price')
        if price:
            try:
                return float(str(price).replace(',', '.'))
            except (ValueError, TypeError):
                return None
        return None

    def _extract_characteristics(self, soup: BeautifulSoup) -> Dict:
        """Извлечение характеристик товара (цвет, материал и т.д.)."""
        characteristics = {}

        # Ищем блок с характеристиками
        specs_section = soup.find('div', class_='k5t1a')
        if not specs_section:
            return characteristics

        # Ищем конкретные характеристики
        spec_items = specs_section.find_all('div', class_='j2l3m')

        for item in spec_items:
            label = item.find('div', class_='n4o5p')
            value = item.find('div', class_='q6r7s')

            if label and value:
                label_text = label.get_text(strip=True).lower()
                value_text = value.get_text(strip=True)

                if 'цвет' in label_text:
                    characteristics['color'] = value_text
                elif 'материал' in label_text:
                    characteristics['material'] = value_text
                elif 'артикул' in label_text or 'комплектация' in label_text:
                    characteristics['manufacturer_sku'] = value_text

        return characteristics

    def _check_rich_content(self, soup: BeautifulSoup) -> bool:
        """Проверка наличия богатого контента (изображения, таблицы, списки в описании)."""
        description_section = soup.find('div', class_='description-content')
        if not description_section:
            return False

        # Проверяем наличие изображений в описании
        images = description_section.find_all('img')
        if images:
            return True

        # Проверяем наличие таблиц
        tables = description_section.find_all('table')
        if tables:
            return True

        # Проверяем наличие списков
        lists = description_section.find_all(['ul', 'ol'])
        if lists:
            return True

        return False

    def parse_products(self, skus: List[str]) -> List[Dict]:
        """Парсинг списка товаров по SKU."""
        results = []

        logger.info(f"Начало парсинга {len(skus)} товаров")

        for i, sku in enumerate(skus, 1):
            logger.info(f"Обработка {i}/{len(skus)}: SKU {sku}")

            product_data = self.parse_product_page(sku)
            if product_data:
                results.append(product_data)

            # Делаем паузу между запросами чтобы не перегружать сервер
            if i < len(skus):
                time.sleep(2)

        logger.info(f"Парсинг завершен. Успешно обработано {len(results)} из {len(skus)} товаров")
        return results

    def save_to_csv(self, products: List[Dict]):
        """Сохранение результатов в CSV файл."""
        if not products:
            logger.warning("Нет данных для сохранения")
            return

        try:
            df = pd.DataFrame(products)

            # Определяем порядок колонок
            columns = [
                'sku', 'title', 'price', 'rating', 'reviews_total',
                'cover_image', 'photos_seller', 'videos_seller',
                'color', 'material', 'art_set', 'has_rich_content'
            ]

            # Переименовываем колонки если нужно
            column_mapping = {
                'reviews_count': 'reviews_total',
                'photos_count': 'photos_seller',
                'videos_count': 'videos_seller',
                'manufacturer_sku': 'art_set'
            }

            df.rename(columns=column_mapping, inplace=True)

            # Выбираем только нужные колонки
            existing_columns = [col for col in columns if col in df.columns]
            df = df[existing_columns]

            df.to_csv(self.output_file, index=False, encoding='utf-8-sig')
            logger.info(f"Данные сохранены в файл {self.output_file}")

        except Exception as e:
            logger.error(f"Ошибка при сохранении в CSV: {e}")


def main():
    """Основная функция для запуска парсера."""
    # Список SKU для парсинга
    skus = ['2359066702', '2829800382']

    parser = OzonParser()

    # Загружаем cookies
    if not parser.load_cookies():
        logger.error("Не удалось загрузить cookies. Запустите сначала get_cookies.py")
        return

    # Парсим товары
    products = parser.parse_products(skus)

    # Сохраняем результаты
    parser.save_to_csv(products)

    logger.info("Работа парсера завершена!")


if __name__ == '__main__':
    main()