#!/usr/bin/env python3
"""
MCP Weather Server - Gets current weather and forecasts using open-meteo.com
Supports stdio, HTTP, and HTTPS transports
Compatible with Claude and n8n
"""

import os
import sys
import asyncio
import argparse
import json
import ipaddress
import logging
from typing import Optional, Dict, Any
from datetime import datetime, timezone
import pytz

import httpx

from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp.types import Tool, TextContent
import mcp.server.stdio

# Configure logging
def setup_logging(log_file: str = "weather_server.log", log_level: int = logging.INFO):
    """Setup logging configuration for both file and stdout"""
    
    # Create formatters
    file_formatter = logging.Formatter(
        fmt='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
        datefmt='%Y-%m-%dT%H:%M:%S'  # ISO format without timezone
    )
    console_formatter = logging.Formatter(
        fmt='%(asctime)s - %(levelname)s - %(message)s',
        datefmt='%H:%M:%S'
    )
    
    # Configure root logger
    logger = logging.getLogger()
    logger.setLevel(log_level)
    
    # Clear any existing handlers
    logger.handlers.clear()
    
    # File handler with ISO timestamp
    file_handler = logging.FileHandler(log_file)
    file_handler.setLevel(log_level)
    file_handler.setFormatter(file_formatter)
    
    # Console handler (stdout)
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setLevel(log_level)
    console_handler.setFormatter(console_formatter)
    
    # Add handlers to logger
    logger.addHandler(file_handler)
    logger.addHandler(console_handler)
    
    return logger

# Initialize logging
logger = setup_logging()

# For HTTP/HTTPS support
try:
    from starlette.applications import Starlette
    from starlette.routing import Route, Mount
    from starlette.responses import Response
    from mcp.server.sse import SseServerTransport
    import uvicorn
    HTTP_AVAILABLE = True
    logger.info("HTTP/HTTPS transport enabled - starlette/uvicorn available")
except ImportError:
    HTTP_AVAILABLE = False
    logger.warning("starlette/uvicorn not available. HTTP/HTTPS transport disabled.")


# Initialize the MCP server
app = Server("weather-server")


class GeolocationService:
    def __init__(self):
        # Use reliable IP geolocation services
        self.geolocation_services = [
            "https://ipapi.co/json/",
            "https://ipinfo.io/json",
            "http://ip-api.com/json/"
        ]
        self.public_ip_services = [
            "https://api.ipify.org?format=json",
            "https://icanhazip.com/",
            "https://checkip.amazonaws.com/"
        ]
        self.logger = logging.getLogger(__name__)
    
    async def is_private_ip(self, ip: str) -> bool:
        """Check if IP address is private (RFC 1918)"""
        try:
            ip_obj = ipaddress.ip_address(ip)
            return ip_obj.is_private
        except ValueError:
            self.logger.warning("Invalid IP address format: %s", ip)
            return False
    
    async def get_public_ip(self) -> Optional[str]:
        """Get public IP address using multiple fallback services"""
        self.logger.debug("Attempting to get public IP address")
        
        for service_url in self.public_ip_services:
            try:
                self.logger.debug("Trying public IP service: %s", service_url)
                async with httpx.AsyncClient() as client:
                    if 'ipify' in service_url or 'ipinfo' in service_url:
                        response = await client.get(service_url, timeout=5.0)
                        response.raise_for_status()
                        data = response.json()
                        ip = data.get('ip')
                        self.logger.info("Successfully obtained public IP: %s from %s", ip, service_url)
                        return ip
                    else:
                        # For simple text responses
                        response = await client.get(service_url, timeout=5.0)
                        response.raise_for_status()
                        ip = response.text.strip()
                        self.logger.info("Successfully obtained public IP: %s from %s", ip, service_url)
                        return ip
            except Exception as e:
                self.logger.warning("Failed to get public IP from %s: %s", service_url, e)
                continue
        
        self.logger.error("All public IP services failed")
        return None
    
    async def get_geolocation_from_ip(self, ip_address: Optional[str] = None) -> Optional[Dict[str, Any]]:
        """Get geolocation data from IP address using multiple fallback services"""
        self.logger.info("Getting geolocation from IP: %s", ip_address or "auto-detect")
        
        # If no IP provided or it's a private IP, get public IP first
        if not ip_address or await self.is_private_ip(ip_address):
            public_ip = await self.get_public_ip()
            if public_ip:
                ip_address = public_ip
                self.logger.info("Using public IP for geolocation: %s", ip_address)
            else:
                self.logger.warning("Using current location (no specific IP)")
                ip_address = None
        
        # Try multiple geolocation services
        for service_url in self.geolocation_services:
            try:
                url = service_url
                if ip_address and 'ip-api.com' not in service_url:
                    # ip-api.com doesn't support specific IP in path
                    url = service_url.replace('/json/', f'/{ip_address}/json/') if ip_address else service_url
                
                self.logger.debug("Trying geolocation service: %s", url)
                async with httpx.AsyncClient() as client:
                    response = await client.get(url, timeout=10.0)
                    response.raise_for_status()
                    data = response.json()
                    
                    # Parse response based on service
                    if 'ipapi.co' in service_url:
                        location = {
                            'city': data.get('city'),
                            'region': data.get('region'),
                            'country': data.get('country_name'),
                            'latitude': data.get('latitude'),
                            'longitude': data.get('longitude'),
                            'ip': data.get('ip')
                        }
                    elif 'ipinfo.io' in service_url:
                        loc = data.get('loc', '').split(',')
                        latitude = float(loc[0]) if loc and len(loc) == 2 else None
                        longitude = float(loc[1]) if loc and len(loc) == 2 else None
                        location = {
                            'city': data.get('city'),
                            'region': data.get('region'),
                            'country': data.get('country'),
                            'latitude': latitude,
                            'longitude': longitude,
                            'ip': data.get('ip')
                        }
                    elif 'ip-api.com' in service_url:
                        location = {
                            'city': data.get('city'),
                            'region': data.get('regionName'),
                            'country': data.get('country'),
                            'latitude': data.get('lat'),
                            'longitude': data.get('lon'),
                            'ip': ip_address if ip_address else data.get('query')
                        }
                    
                    # Validate we have coordinates
                    if location.get('latitude') and location.get('longitude'):
                        self.logger.info(
                            "Successfully got location from %s: %s, %s (%s, %s)",
                            service_url,
                            location.get('city', 'Unknown'),
                            location.get('country', 'Unknown'),
                            location.get('latitude'),
                            location.get('longitude')
                        )
                        return location
                    else:
                        self.logger.warning("No coordinates from %s", service_url)
                        continue
                        
            except Exception as e:
                self.logger.warning("Failed geolocation service %s: %s", service_url, e)
                continue
        
        self.logger.error("All geolocation services failed")
        return None
    
    async def get_geolocation_from_name(self, location_name: str) -> Optional[Dict[str, Any]]:
        """Get coordinates from location name using Open-Meteo's geocoding"""
        self.logger.info("Geocoding location name: %s", location_name)
        
        try:
            url = "https://geocoding-api.open-meteo.com/v1/search"
            params = {
                'name': location_name,
                'count': 5,  # Get more results for better matching
                'language': 'en',
                'format': 'json'
            }
            
            self.logger.debug("Calling Open-Meteo geocoding API with params: %s", params)
            async with httpx.AsyncClient() as client:
                response = await client.get(url, params=params, timeout=10.0)
                response.raise_for_status()
                data = response.json()
                
                if data.get('results') and len(data['results']) > 0:
                    # Return the first/best match
                    result = data['results'][0]
                    location_data = {
                        'name': result.get('name'),
                        'country': result.get('country'),
                        'latitude': result.get('latitude'),
                        'longitude': result.get('longitude'),
                        'timezone': result.get('timezone'),
                        'admin1': result.get('admin1', '')  # State/region
                    }
                    self.logger.info(
                        "Found location: %s, %s (%s, %s)",
                        location_data['name'],
                        location_data['country'],
                        location_data['latitude'],
                        location_data['longitude']
                    )
                    return location_data
                else:
                    self.logger.warning("No results found for location: %s", location_name)
                    # Try with a simpler query
                    if ',' in location_name:
                        simple_name = location_name.split(',')[0].strip()
                        if simple_name != location_name:
                            self.logger.info("Trying simpler query: %s", simple_name)
                            return await self.get_geolocation_from_name(simple_name)
                    return None
                    
        except Exception as e:
            self.logger.error("Error geocoding location name '%s': %s", location_name, e)
            return None


class TimeService:
    def __init__(self):
        self.geolocation = GeolocationService()
        self.logger = logging.getLogger(__name__)
        try:
            from num2words import num2words
            self.num2words = num2words
        except ImportError:
            self.logger.error("num2words not installed. Please run: pip install num2words")
            raise
    
    def _get_month_name(self, month: int) -> str:
        """Convert month number to name"""
        months = [
            "January", "February", "March", "April", "May", "June",
            "July", "August", "September", "October", "November", "December"
        ]
        return months[month - 1]
    
    def _get_day_ordinal(self, day: int) -> str:
        """Convert day number to ordinal using num2words"""
        try:
            return self.num2words(day, to='ordinal')
        except:
            # Fallback
            if 11 <= day <= 13:
                return f"{day}th"
            elif day % 10 == 1:
                return f"{day}st"
            elif day % 10 == 2:
                return f"{day}nd"
            elif day % 10 == 3:
                return f"{day}rd"
            else:
                return f"{day}th"
    
    def _format_time_words(self, hour: int, minute: int, language: str = 'en') -> str:
        """Format time in words using num2words with language support"""
        # Convert to 12-hour format
        if hour == 0 or hour == 12:
            hour_12 = 12
            period = "am" if hour == 0 else "pm"
        else:
            hour_12 = hour % 12
            period = "am" if hour < 12 else "pm"
        
        # Convert numbers to words with specified language
        hour_word = self.num2words(hour_12, lang=language)
        
        if minute == 0:
            return f"{hour_word} {period}"
        elif minute < 10:
            minute_word = self.num2words(minute, lang=language)
            return f"{hour_word} oh {minute_word} {period}"
        else:
            minute_word = self.num2words(minute, lang=language)
            return f"{hour_word} {minute_word} {period}"
    
    async def get_current_time_for_location(self, location_name: str = None, client_ip: str = None, language: str = 'en') -> Dict[str, Any]:
        """Get current time for a location with language support"""
        self.logger.info("Getting current time for location: %s, IP: %s, language: %s", 
                        location_name, client_ip, language)
        
        # Get location data (same as before)
        if location_name:
            geolocation = await self.geolocation.get_geolocation_from_name(location_name)
            if not geolocation:
                raise ValueError(f"Could not find location: {location_name}")
        else:
            geolocation = await self.geolocation.get_geolocation_from_ip(client_ip)
            if not geolocation:
                geolocation = {'city': 'Unknown', 'country': 'Unknown', 'timezone': 'UTC'}
        
        # Get timezone and current time (same as before)
        timezone_str = geolocation.get('timezone', 'UTC')
        try:
            tz = pytz.timezone(timezone_str)
        except:
            tz = pytz.UTC
        
        current_time = datetime.now(tz)
        
        # Format with language support
        hour = current_time.hour
        minute = current_time.minute
        month = current_time.month
        day = current_time.day
        
        time_words = self._format_time_words(hour, minute, language)
        month_name = self._get_month_name(month)
        day_ordinal = self._get_day_ordinal(day)
        
        location_display = geolocation.get('city', 'Unknown')
        if geolocation.get('country') and geolocation['country'] != 'Unknown':
            location_display += f", {geolocation['country']}"
        
        spoken_time = f"the current time is {time_words} {month_name} {day_ordinal} in {location_display}"
        
        return {
            'spoken_time': spoken_time,
            'location': location_display,
            'timezone': timezone_str,
            'iso_time': current_time.isoformat(),
            'hour': hour,
            'minute': minute,
            'month': month,
            'day': day,
            'year': current_time.year
        }


class WeatherService:
    def __init__(self):
        self.base_url = "https://api.open-meteo.com/v1"
        self.geolocation = GeolocationService()
        self.time_service = TimeService()
        self.logger = logging.getLogger(__name__)
    
    async def get_current_weather(self, latitude: float, longitude: float) -> Optional[Dict[str, Any]]:
        """Get current weather data for coordinates"""
        self.logger.info("Getting current weather for coordinates: %s, %s", latitude, longitude)
        
        try:
            url = f"{self.base_url}/forecast"
            params = {
                'latitude': latitude,
                'longitude': longitude,
                'current': 'temperature_2m,relative_humidity_2m,apparent_temperature,is_day,precipitation,rain,showers,snowfall,weather_code,cloud_cover,pressure_msl,surface_pressure,wind_speed_10m,wind_direction_10m,wind_gusts_10m',
                'timezone': 'auto',
                'forecast_days': 1
            }
            
            self.logger.debug("Calling Open-Meteo current weather API with params: %s", params)
            async with httpx.AsyncClient() as client:
                response = await client.get(url, params=params, timeout=10.0)
                response.raise_for_status()
                data = response.json()
                
                self.logger.info("Successfully retrieved current weather data")
                return self._format_current_weather(data)
                
        except Exception as e:
            self.logger.error("Error getting current weather: %s", e)
            return None
    
    async def get_forecast(self, latitude: float, longitude: float, days: int = 3) -> Optional[Dict[str, Any]]:
        """Get weather forecast for coordinates"""
        self.logger.info("Getting %s-day forecast for coordinates: %s, %s", days, latitude, longitude)
        
        try:
            url = f"{self.base_url}/forecast"
            params = {
                'latitude': latitude,
                'longitude': longitude,
                'daily': 'weather_code,temperature_2m_max,temperature_2m_min,apparent_temperature_max,apparent_temperature_min,sunrise,sunset,precipitation_sum,rain_sum,showers_sum,snowfall_sum,precipitation_hours,precipitation_probability_max,wind_speed_10m_max,wind_gusts_10m_max,wind_direction_10m_dominant',
                'timezone': 'auto',
                'forecast_days': days
            }
            
            self.logger.debug("Calling Open-Meteo forecast API with params: %s", params)
            async with httpx.AsyncClient() as client:
                response = await client.get(url, params=params, timeout=10.0)
                response.raise_for_status()
                data = response.json()
                
                self.logger.info("Successfully retrieved %s-day forecast data", days)
                return self._format_forecast(data)
                
        except Exception as e:
            self.logger.error("Error getting forecast: %s", e)
            return None
    
    def _format_current_weather(self, data: Dict[str, Any]) -> Dict[str, Any]:
        """Format current weather data"""
        current = data.get('current', {})
        current_units = data.get('current_units', {})
        
        return {
            'timestamp': current.get('time', ''),
            'temperature': current.get('temperature_2m'),
            'temperature_unit': current_units.get('temperature_2m', '°C'),
            'apparent_temperature': current.get('apparent_temperature'),
            'relative_humidity': current.get('relative_humidity_2m'),
            'humidity_unit': current_units.get('relative_humidity_2m', '%'),
            'weather_code': current.get('weather_code'),
            'weather_description': self._get_weather_description(current.get('weather_code')),
            'is_day': current.get('is_day'),
            'precipitation': current.get('precipitation'),
            'precipitation_unit': current_units.get('precipitation', 'mm'),
            'rain': current.get('rain'),
            'snowfall': current.get('snowfall'),
            'cloud_cover': current.get('cloud_cover'),
            'cloud_cover_unit': current_units.get('cloud_cover', '%'),
            'pressure': current.get('pressure_msl'),
            'pressure_unit': current_units.get('pressure_msl', 'hPa'),
            'wind_speed': current.get('wind_speed_10m'),
            'wind_speed_unit': current_units.get('wind_speed_10m', 'km/h'),
            'wind_direction': current.get('wind_direction_10m'),
            'wind_gusts': current.get('wind_gusts_10m'),
            'location': {
                'latitude': data.get('latitude'),
                'longitude': data.get('longitude'),
                'timezone': data.get('timezone')
            }
        }
    
    def _format_forecast(self, data: Dict[str, Any]) -> Dict[str, Any]:
        """Format forecast data"""
        daily = data.get('daily', {})
        daily_units = data.get('daily_units', {})
        
        forecast_days = []
        for i in range(len(daily.get('time', []))):
            day_data = {
                'date': daily['time'][i],
                'weather_code': daily['weather_code'][i],
                'weather_description': self._get_weather_description(daily['weather_code'][i]),
                'temperature_max': daily['temperature_2m_max'][i],
                'temperature_min': daily['temperature_2m_min'][i],
                'temperature_unit': daily_units.get('temperature_2m_max', '°C'),
                'apparent_temperature_max': daily['apparent_temperature_max'][i],
                'apparent_temperature_min': daily['apparent_temperature_min'][i],
                'sunrise': daily['sunrise'][i],
                'sunset': daily['sunset'][i],
                'precipitation_sum': daily['precipitation_sum'][i],
                'precipitation_unit': daily_units.get('precipitation_sum', 'mm'),
                'precipitation_probability': daily.get('precipitation_probability_max', [])[i] if daily.get('precipitation_probability_max') else None,
                'wind_speed_max': daily['wind_speed_10m_max'][i],
                'wind_speed_unit': daily_units.get('wind_speed_10m_max', 'km/h'),
                'wind_gusts_max': daily['wind_gusts_10m_max'][i]
            }
            forecast_days.append(day_data)
        
        return {
            'location': {
                'latitude': data.get('latitude'),
                'longitude': data.get('longitude'),
                'timezone': data.get('timezone')
            },
            'forecast': forecast_days
        }
    
    def _get_weather_description(self, code: int) -> str:
        """Convert weather code to human-readable description"""
        weather_codes = {
            0: "Clear sky",
            1: "Mainly clear", 
            2: "Partly cloudy",
            3: "Overcast",
            45: "Fog",
            48: "Depositing rime fog",
            51: "Light drizzle",
            53: "Moderate drizzle",
            55: "Dense drizzle",
            56: "Light freezing drizzle",
            57: "Dense freezing drizzle",
            61: "Slight rain",
            63: "Moderate rain",
            65: "Heavy rain",
            66: "Light freezing rain",
            67: "Heavy freezing rain",
            71: "Slight snow fall",
            73: "Moderate snow fall",
            75: "Heavy snow fall",
            77: "Snow grains",
            80: "Slight rain showers",
            81: "Moderate rain showers",
            82: "Violent rain showers",
            85: "Slight snow showers",
            86: "Heavy snow showers",
            95: "Thunderstorm",
            96: "Thunderstorm with slight hail",
            99: "Thunderstorm with heavy hail"
        }
        return weather_codes.get(code, "Unknown")


# Initialize services
weather_service = WeatherService()
time_service = TimeService()


async def get_coordinates(arguments: Dict[str, Any]) -> tuple[float, float, str]:
    """Get coordinates from arguments or fall back to IP geolocation"""
    location_name = arguments.get('location_name')
    latitude = arguments.get('latitude')
    longitude = arguments.get('longitude')
    client_ip = arguments.get('client_ip')
    
    logger.info(
        "Getting coordinates - location_name: %s, latitude: %s, longitude: %s, client_ip: %s",
        location_name, latitude, longitude, client_ip
    )
    
    # If coordinates provided directly, use them
    if latitude is not None and longitude is not None:
        location_info = f"Coordinates: {latitude}, {longitude}"
        logger.info("Using provided coordinates: %s, %s", latitude, longitude)
        return float(latitude), float(longitude), location_info
    
    # If location name provided, geocode it
    if location_name:
        logger.info("Geocoding location name: %s", location_name)
        geolocation = await weather_service.geolocation.get_geolocation_from_name(location_name)
        if geolocation:
            location_info = f"{geolocation.get('name', 'Unknown')}"
            if geolocation.get('admin1'):
                location_info += f", {geolocation['admin1']}"
            if geolocation.get('country'):
                location_info += f", {geolocation['country']}"
            logger.info("Successfully geocoded location: %s", location_info)
            return geolocation['latitude'], geolocation['longitude'], location_info
        else:
            logger.error("Could not find location: %s", location_name)
            raise ValueError(f"Could not find location: {location_name}")
    
    # Fall back to IP-based geolocation
    logger.info("Falling back to IP-based geolocation with IP: %s", client_ip or "auto-detect")
    geolocation = await weather_service.geolocation.get_geolocation_from_ip(client_ip)
    if geolocation:
        location_info = f"{geolocation.get('city', 'Unknown')}, {geolocation.get('country', 'Unknown')} (IP-based)"
        logger.info("Successfully obtained location from IP: %s", location_info)
        return geolocation['latitude'], geolocation['longitude'], location_info
    else:
        # Final fallback - use a default location
        logger.warning("Using default location (New York) as fallback")
        return 40.7128, -74.0060, "New York, USA (default fallback)"


@app.list_tools()
async def list_tools() -> list[Tool]:
    """List available tools"""
    logger.info("Listing available tools")
    return [
        Tool(
            name="get_current_weather",
            description="Get current weather for a location. If no location provided, uses IP-based geolocation.",
            inputSchema={
                "type": "object",
                "properties": {
                    "location_name": {
                        "type": "string",
                        "description": "Location name (e.g., 'London, UK', 'New York')"
                    },
                    "latitude": {
                        "type": "number", 
                        "description": "Latitude coordinate"
                    },
                    "longitude": {
                        "type": "number",
                        "description": "Longitude coordinate"
                    },
                    "client_ip": {
                        "type": "string",
                        "description": "Client IP address for geolocation (used if no location provided)"
                    }
                },
                "required": []
            }
        ),
        Tool(
            name="get_weather_forecast", 
            description="Get weather forecast for a location. If no location provided, uses IP-based geolocation.",
            inputSchema={
                "type": "object",
                "properties": {
                    "location_name": {
                        "type": "string",
                        "description": "Location name (e.g., 'London, UK', 'New York')"
                    },
                    "latitude": {
                        "type": "number",
                        "description": "Latitude coordinate"
                    },
                    "longitude": {
                        "type": "number", 
                        "description": "Longitude coordinate"
                    },
                    "days": {
                        "type": "number",
                        "description": "Number of forecast days (1-7, default: 3)"
                    },
                    "client_ip": {
                        "type": "string", 
                        "description": "Client IP address for geolocation (used if no location provided)"
                    }
                },
                "required": []
            }
        ),
        Tool(
            name="get_current_time",
            description="Get current time and date for a location in spoken format. If no location provided, uses IP-based geolocation.",
            inputSchema={
                "type": "object",
                "properties": {
                    "location_name": {
                        "type": "string",
                        "description": "Location name (e.g., 'London, UK', 'New York')"
                    },
                    "client_ip": {
                        "type": "string",
                        "description": "Client IP address for geolocation (used if no location provided)"
                    }
                },
                "required": []
            }
        )
    ]


@app.call_tool()
async def call_tool(name: str, arguments: dict) -> list[TextContent]:
    """Handle tool calls"""
    logger.info("Tool call received - name: %s, arguments: %s", name, arguments)

    if name == "get_current_weather":
        try:
            latitude, longitude, location_info = await get_coordinates(arguments)
            
            logger.info("Fetching current weather for %s", location_info)
            weather_data = await weather_service.get_current_weather(latitude, longitude)
            if not weather_data:
                logger.error("Failed to fetch current weather data")
                return [TextContent(
                    type="text",
                    text="Error: Failed to fetch weather data"
                )]
            
            # Format the response
            current = weather_data
            text = f"""# Current Weather - {location_info}

**Temperature**: {current['temperature']}{current['temperature_unit']} (Feels like {current['apparent_temperature']}{current['temperature_unit']})
**Conditions**: {current['weather_description']}
**Humidity**: {current['relative_humidity']}{current['humidity_unit']}
**Cloud Cover**: {current['cloud_cover']}{current['cloud_cover_unit']}
**Pressure**: {current['pressure']}{current['pressure_unit']}
**Wind**: {current['wind_speed']}{current['wind_speed_unit']} from {current['wind_direction']}° direction
**Precipitation**: {current['precipitation']}{current['precipitation_unit']}
**Rain**: {current['rain']}{current['precipitation_unit']}
**Snowfall**: {current['snowfall']}{current['precipitation_unit']}

*Location*: {current['location']['latitude']:.4f}, {current['location']['longitude']:.4f}
*Timezone*: {current['location']['timezone']}
*Last Updated*: {current['timestamp']}"""
            
            logger.info("Successfully returned current weather data")
            return [TextContent(type="text", text=text)]
            
        except Exception as e:
            logger.error("Error getting current weather: %s", e, exc_info=True)
            return [TextContent(
                type="text",
                text=f"Error getting current weather: {str(e)}"
            )]

    elif name == "get_weather_forecast":
        try:
            latitude, longitude, location_info = await get_coordinates(arguments)
            days = min(max(int(arguments.get('days', 3)), 1), 7)  # Clamp between 1-7 days
            
            logger.info("Fetching %s-day forecast for %s", days, location_info)
            forecast_data = await weather_service.get_forecast(latitude, longitude, days)
            if not forecast_data:
                logger.error("Failed to fetch forecast data")
                return [TextContent(
                    type="text",
                    text="Error: Failed to fetch forecast data"
                )]
            
            # Format the response
            text = f"# {days}-Day Weather Forecast - {location_info}\n\n"
            
            for i, day in enumerate(forecast_data['forecast']):
                text += f"## {day['date']}\n"
                text += f"**Conditions**: {day['weather_description']}\n"
                text += f"**Temperature**: {day['temperature_min']} to {day['temperature_max']}{day['temperature_unit']}\n"
                text += f"**Feels like**: {day['apparent_temperature_min']} to {day['apparent_temperature_max']}{day['temperature_unit']}\n"
                text += f"**Precipitation**: {day['precipitation_sum']}{day['precipitation_unit']}"
                if day['precipitation_probability']:
                    text += f" ({day['precipitation_probability']}% chance)"
                text += f"\n**Wind**: Up to {day['wind_speed_max']}{day['wind_speed_unit']} with gusts to {day['wind_gusts_max']}{day['wind_speed_unit']}\n"
                text += f"**Sunrise**: {day['sunrise'][11:]} | **Sunset**: {day['sunset'][11:]}\n\n"
            
            text += f"*Location*: {forecast_data['location']['latitude']:.4f}, {forecast_data['location']['longitude']:.4f}\n"
            text += f"*Timezone*: {forecast_data['location']['timezone']}"
            
            logger.info("Successfully returned %s-day forecast data", days)
            return [TextContent(type="text", text=text)]
            
        except Exception as e:
            logger.error("Error getting weather forecast: %s", e, exc_info=True)
            return [TextContent(
                type="text",
                text=f"Error getting weather forecast: {str(e)}"
            )]

    elif name == "get_current_time":
        try:
            location_name = arguments.get('location_name')
            client_ip = arguments.get('client_ip')
            
            logger.info("Getting current time for location: %s, IP: %s", location_name, client_ip)
            time_data = await time_service.get_current_time_for_location(location_name, client_ip)
            
            text = f"""# Current Time

{time_data['spoken_time'].capitalize()}

*Detailed Information:*
- **Location**: {time_data['location']}
- **Timezone**: {time_data['timezone']}
- **ISO Time**: {time_data['iso_time']}
- **Date**: {time_data['month']}/{time_data['day']}/{time_data['year']}
- **Time**: {time_data['hour']:02d}:{time_data['minute']:02d}"""
            
            logger.info("Successfully returned current time data")
            return [TextContent(type="text", text=text)]
            
        except Exception as e:
            logger.error("Error getting current time: %s", e, exc_info=True)
            return [TextContent(
                type="text",
                text=f"Error getting current time: {str(e)}"
            )]

    else:
        logger.warning("Unknown tool called: %s", name)
        return [TextContent(
            type="text",
            text=f"Unknown tool: {name}"
        )]


async def run_stdio():
    """Run the server using stdio transport"""
    logger.info("Starting MCP Weather Server with stdio transport")
    async with stdio_server() as streams:
        await app.run(
            streams[0],
            streams[1],
            app.create_initialization_options()
        )


def create_sse_app():
    """Create Starlette app for SSE transport (HTTP/HTTPS)"""
    if not HTTP_AVAILABLE:
        raise RuntimeError("HTTP transport not available. Install: pip install starlette uvicorn sse-starlette")

    sse = SseServerTransport("/messages/")

    async def handle_sse(request):
        logger.info("SSE connection established from %s", request.client.host)
        async with sse.connect_sse(
            request.scope,
            request.receive,
            request._send
        ) as streams:
            await app.run(
                streams[0],
                streams[1],
                app.create_initialization_options()
            )
        return Response()

    starlette_app = Starlette(
        debug=True,
        routes=[
            Route("/sse", endpoint=handle_sse, methods=["GET"]),
            Mount("/messages/", app=sse.handle_post_message),
        ]
    )

    return starlette_app


def run_http_server(host: str, port: int, use_ssl: bool = False, certfile: str = None, keyfile: str = None):
    """Run the server using HTTP/HTTPS with SSE transport"""
    if not HTTP_AVAILABLE:
        logger.error("HTTP transport not available. Install: pip install starlette uvicorn sse-starlette")
        sys.exit(1)

    starlette_app = create_sse_app()

    config = {
        "app": starlette_app,
        "host": host,
        "port": port,
    }

    if use_ssl:
        if not certfile or not keyfile:
            logger.error("SSL enabled but certificate/key files not provided")
            sys.exit(1)
        config["ssl_certfile"] = certfile
        config["ssl_keyfile"] = keyfile
        protocol = "https"
        logger.info("SSL enabled with certfile: %s, keyfile: %s", certfile, keyfile)
    else:
        protocol = "http"

    logger.info("Starting MCP Weather Server on %s://%s:%s", protocol, host, port)
    logger.info("SSE endpoint: %s://%s:%s/sse", protocol, host, port)

    uvicorn.run(**config)


def main():
    """Main entry point with argument parsing"""
    parser = argparse.ArgumentParser(
        description="MCP Weather Server - Get current weather and forecasts",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Run with stdio (for Claude Desktop, etc.)
  python weather_server.py --transport stdio

  # Run with HTTP
  python weather_server.py --transport http --host 0.0.0.0 --port 8000

  # Run with HTTPS
  python weather_server.py --transport https --host 0.0.0.0 --port 8443 \\
      --certfile cert.pem --keyfile key.pem
        """
    )

    parser.add_argument(
        "--transport",
        choices=["stdio", "http", "https"],
        default="stdio",
        help="Transport protocol to use (default: stdio)"
    )
    parser.add_argument(
        "--host",
        default="0.0.0.0",
        help="Host to bind to for HTTP/HTTPS (default: 0.0.0.0)"
    )
    parser.add_argument(
        "--port",
        type=int,
        default=3100,
        help="Port to bind to for HTTP/HTTPS (default: 3100)"
    )
    parser.add_argument(
        "--certfile",
        help="SSL certificate file for HTTPS"
    )
    parser.add_argument(
        "--keyfile",
        help="SSL key file for HTTPS"
    )
    parser.add_argument(
        "--log-file",
        default="weather_server.log",
        help="Log file path (default: weather_server.log)"
    )
    parser.add_argument(
        "--log-level",
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
        default="INFO",
        help="Log level (default: INFO)"
    )

    args = parser.parse_args()

    # Reconfigure logging with command line arguments
    log_level = getattr(logging, args.log_level)
    setup_logging(args.log_file, log_level)
    
    logger.info("MCP Weather Server starting with transport: %s", args.transport)
    logger.debug("Command line arguments: %s", vars(args))

    if args.transport == "stdio":
        asyncio.run(run_stdio())
    elif args.transport in ["http", "https"]:
        use_ssl = args.transport == "https"
        run_http_server(args.host, args.port, use_ssl, args.certfile, args.keyfile)


if __name__ == "__main__":
    main()

