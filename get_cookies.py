"""
Скрипт для автоматической авторизации на Ozon через номер телефона
и извлечения кода подтверждения из Gmail.
Сохраняет полученные cookies в файл для последующего использования.
"""

import os
import re
import time
import json
import logging
from pathlib import Path
from typing import Optional

import requests
from google.auth.transport.requests import AuthorizedSession
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from bs4 import BeautifulSoup

# Настройка логирования
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


class OzonAuthenticator:
    """Класс для авторизации на Ozon и сохранения cookies."""

    def __init__(self):
        self.phone_number = os.getenv('OZON_PHONE', '+79991234567')
        self.session = requests.Session()
        self.cookies_file = 'ozon_cookies.json'

        # Заголовки для имитации браузера
        self.headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
            'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8',
            'Accept-Language': 'ru-RU,ru;q=0.9,en-US;q=0.8,en;q=0.7',
        }
        self.session.headers.update(self.headers)

    def get_gmail_service(self) -> Optional[build]:
        """Инициализация сервиса Gmail API."""
        try:
            credentials = Credentials.from_authorized_user_file(
                'gmail_token.json',
                ['https://www.googleapis.com/auth/gmail.readonly']
            )

            if not credentials or not credentials.valid:
                logger.error("Не удалось загрузить credentials для Gmail API")
                return None

            service = build('gmail', 'v1', credentials=credentials)
            logger.info("Gmail API успешно инициализирован")
            return service

        except Exception as e:
            logger.error(f"Ошибка при инициализации Gmail API: {e}")
            return None

    def get_verification_code_from_gmail(self, service: build) -> Optional[str]:
        """Получение кода подтверждения из последнего письма от Ozon."""
        try:
            # Получаем последние 5 непрочитанных сообщений от Ozon
            results = service.users().messages().list(
                userId='me',
                q='from:noreply@ozon.ru is:unread',
                maxResults=5
            ).execute()

            messages = results.get('messages', [])

            if not messages:
                logger.warning("Не найдено непрочитанных писем от Ozon")
                return None

            for message in messages:
                msg_id = message['id']
                msg = service.users().messages().get(
                    userId='me',
                    id=msg_id,
                    format='full'
                ).execute()

                # Извлекаем тело письма
                body = self._extract_email_body(msg)

                if body:
                    # Ищем код подтверждения (обычно 6 цифр)
                    code_match = re.search(r'\b(\d{6})\b', body)
                    if code_match:
                        code = code_match.group(1)
                        logger.info(f"Найден код подтверждения: {code}")

                        # Помечаем письмо как прочитанное
                        service.users().messages().modify(
                            userId='me',
                            id=msg_id,
                            body={'removeLabelIds': ['UNREAD']}
                        ).execute()

                        return code

            logger.warning("Код подтверждения не найден в письмах")
            return None

        except Exception as e:
            logger.error(f"Ошибка при получении кода из Gmail: {e}")
            return None

    def _extract_email_body(self, message: dict) -> Optional[str]:
        """Извлечение тела письма из сообщения Gmail."""
        try:
            parts = message.get('payload', {}).get('parts', [])

            for part in parts:
                if part.get('mimeType') == 'text/plain':
                    data = part.get('body', {}).get('data', '')
                    if data:
                        import base64
                        decoded_data = base64.urlsafe_b64decode(data).decode('utf-8')
                        return decoded_data

                # Проверяем вложенные части
                if 'parts' in part:
                    for sub_part in part['parts']:
                        if sub_part.get('mimeType') == 'text/plain':
                            data = sub_part.get('body', {}).get('data', '')
                            if data:
                                import base64
                                decoded_data = base64.urlsafe_b64decode(data).decode('utf-8')
                                return decoded_data

            return None

        except Exception as e:
            logger.error(f"Ошибка при извлечении тела письма: {e}")
            return None

    def initiate_login(self) -> bool:
        """Начало процесса авторизации - отправка запроса на получение кода."""
        try:
            # Переходим на страницу авторизации
            login_url = 'https://www.ozon.ru/login/'
            response = self.session.get(login_url)

            if response.status_code != 200:
                logger.error(f"Не удалось получить страницу авторизации: {response.status_code}")
                return False

            logger.info("Страница авторизации загружена")

            # Отправляем номер телефона для получения кода
            # Примечание: точный endpoint может меняться, нужно актуализировать
            send_code_url = 'https://www.ozon.ru/api/entry/v1/phone-code'

            payload = {
                'phone': self.phone_number.replace('+', ''),
                'is_webview': False
            }

            response = self.session.post(send_code_url, json=payload)

            if response.status_code == 200:
                logger.info(f"Код отправлен на номер {self.phone_number}")
                return True
            else:
                logger.error(f"Ошибка при отправке кода: {response.status_code} - {response.text}")
                return False

        except Exception as e:
            logger.error(f"Ошибка при инициировании входа: {e}")
            return False

    def complete_login(self, verification_code: str) -> bool:
        """Завершение авторизации с использованием кода подтверждения."""
        try:
            # Подтверждаем код
            verify_url = 'https://www.ozon.ru/api/entry/v1/verify-phone-code'

            payload = {
                'phone': self.phone_number.replace('+', ''),
                'code': verification_code,
                'is_webview': False
            }

            response = self.session.post(verify_url, json=payload)

            if response.status_code == 200:
                logger.info("Авторизация успешна!")
                self.save_cookies()
                return True
            else:
                logger.error(f"Ошибка при подтверждении кода: {response.status_code} - {response.text}")
                return False

        except Exception as e:
            logger.error(f"Ошибка при завершении авторизации: {e}")
            return False

    def save_cookies(self):
        """Сохранение cookies в файл."""
        try:
            cookies_dict = {cookie.name: cookie.value for cookie in self.session.cookies}

            with open(self.cookies_file, 'w', encoding='utf-8') as f:
                json.dump(cookies_dict, f, ensure_ascii=False, indent=2)

            logger.info(f"Cookies сохранены в файл {self.cookies_file}")

        except Exception as e:
            logger.error(f"Ошибка при сохранении cookies: {e}")

    def load_cookies(self) -> bool:
        """Загрузка cookies из файла."""
        try:
            if not Path(self.cookies_file).exists():
                logger.warning(f"Файл cookies {self.cookies_file} не найден")
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

    def authenticate(self) -> bool:
        """Полный процесс авторизации."""
        logger.info("Начало процесса авторизации на Ozon")

        # Шаг 1: Инициируем вход
        if not self.initiate_login():
            return False

        # Шаг 2: Ждем и получаем код из Gmail
        logger.info("Ожидание получения кода подтверждения...")
        time.sleep(5)  # Ждем несколько секунд для доставки письма

        gmail_service = self.get_gmail_service()
        if not gmail_service:
            return False

        verification_code = self.get_verification_code_from_gmail(gmail_service)
        if not verification_code:
            logger.error("Не удалось получить код подтверждения")
            return False

        # Шаг 3: Завершаем авторизацию
        if not self.complete_login(verification_code):
            return False

        logger.info("Авторизация успешно завершена!")
        return True


def main():
    """Основная функция для запуска авторизации."""
    authenticator = OzonAuthenticator()

    # Проверяем, есть ли уже сохраненные cookies
    if authenticator.load_cookies():
        logger.info("Используем существующие cookies")
        return True

    # Выполняем полную авторизацию
    success = authenticator.authenticate()

    if success:
        logger.info("Готово! Cookies сохранены и готовы к использованию.")
    else:
        logger.error("Авторизация не удалась.")

    return success


if __name__ == '__main__':
    main()