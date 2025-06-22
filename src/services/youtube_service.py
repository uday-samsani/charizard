"""
Enhanced YouTube Service with Transcript and Advanced Analytics
"""

import asyncio
from typing import Dict, Any, List, Optional
import logging
import re
import json
from datetime import datetime, timedelta
from urllib.parse import urlparse, parse_qs
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError
import nltk
from nltk.sentiment import SentimentIntensityAnalyzer
import langid
import requests
from youtube_transcript_api import YouTubeTranscriptApi
from youtube_transcript_api.formatters import TextFormatter

logger = logging.getLogger(__name__)

# Download required NLTK data
try:
    nltk.data.find('vader_lexicon')
except LookupError:
    print("Downloading VADER lexicon...")
    nltk.download('vader_lexicon')


class EnhancedYouTubeService:
    """Enhanced YouTube service with transcript and advanced analytics capabilities."""
    
    def __init__(self, api_key: str):
        self.api_keys = [api_key]  # Primary API key
        self.current_key_index = 0
        self.api_key = api_key
        self.youtube = build("youtube", "v3", developerKey=api_key)
        self.sia = SentimentIntensityAnalyzer()
        
        # Enhanced sarcasm detection
        self.negative_context_keywords = [
            'clickbait', 'waste of time', 'fake', 'scam', 'slow', 'unwatchable',
            'ads', 'noise', 'laggy', 'again', 'great job', 'thanks a lot',
            'as always', 'fell off', 'boring', 'skip', 'trash', 'garbage'
        ]
        
        self.sarcasm_indicators = [
            'sure', 'totally', 'wow', 'thanks a lot', 'great job', 'genius',
            'lmao', 'lol', 'yeah right', 'can\'t wait', 'so helpful', 'this aged well',
            'obviously', 'clearly', 'of course', 'naturally'
        ]
        
        self.emoji_indicators = ['🙄', '😒', '🤡', '😂', '🤣', '😑', '😏', '😤']
        
        # Question detection patterns
        self.question_patterns = [
            r'\?$',  # Ends with question mark
            r'\b(how|what|when|where|why|who|which|whose|whom)\b',
            r'\b(can you|could you|would you|will you)\b',
            r'\b(do you|does it|is it|are you)\b'
        ]
    
    def add_api_key(self, api_key: str):
        """Add an additional API key for rotation"""
        if api_key not in self.api_keys:
            self.api_keys.append(api_key)
            logger.info(f"Added API key for rotation. Total keys: {len(self.api_keys)}")

    def rotate_api_key(self):
        """Rotate to the next available API key"""
        if len(self.api_keys) > 1:
            self.current_key_index = (self.current_key_index + 1) % len(self.api_keys)
            self.api_key = self.api_keys[self.current_key_index]
            self.youtube = build("youtube", "v3", developerKey=self.api_key)
            logger.info(f"Rotated to API key {self.current_key_index + 1}/{len(self.api_keys)}")
            return True
        return False

    def execute_with_retry(self, request_func, *args, **kwargs):
        """Execute a YouTube API request with automatic retry and key rotation"""
        max_retries = len(self.api_keys)
        
        for attempt in range(max_retries):
            try:
                return request_func(*args, **kwargs)
            except Exception as e:
                error_str = str(e).lower()
                if 'quota' in error_str and 'exceeded' in error_str:
                    logger.warning(f"Quota exceeded with API key {self.current_key_index + 1}")
                    if self.rotate_api_key():
                        continue
                    else:
                        raise Exception("All API keys have exceeded quota")
                else:
                    raise e
        
        raise Exception("All API keys failed")
    
    def extract_video_id(self, url: str) -> Optional[str]:
        """Extract video ID from various YouTube URL formats."""
        try:
            parsed_url = urlparse(url)
            
            # Handle youtu.be short links
            if parsed_url.hostname in ["youtu.be"]:
                return parsed_url.path.lstrip("/")
            
            # Handle full length YouTube links
            elif parsed_url.hostname in ["www.youtube.com", "youtube.com"]:
                query = parse_qs(parsed_url.query)
                return query.get("v", [None])[0]
            
            # Handle mobile YouTube links
            elif parsed_url.hostname in ["m.youtube.com"]:
                query = parse_qs(parsed_url.query)
                return query.get("v", [None])[0]
            
            return None
        except Exception as e:
            logger.error(f"Error extracting video ID: {e}")
            return None
    
    def get_video_info(self, video_id: str) -> Optional[Dict[str, Any]]:
        """Get comprehensive video information."""
        try:
            request = self.youtube.videos().list(
                part="snippet,statistics,contentDetails",
                id=video_id
            )
            response = request.execute()
            
            if response['items']:
                video = response['items'][0]
                snippet = video['snippet']
                statistics = video['statistics']
                content_details = video['contentDetails']
                
                return {
                    'title': snippet['title'],
                    'description': snippet['description'][:500] + '...' if len(snippet['description']) > 500 else snippet['description'],
                    'channel': snippet['channelTitle'],
                    'channel_id': snippet['channelId'],
                    'published_at': snippet['publishedAt'],
                    'view_count': int(statistics.get('viewCount', 0)),
                    'like_count': int(statistics.get('likeCount', 0)),
                    'comment_count': int(statistics.get('commentCount', 0)),
                    'thumbnail': snippet['thumbnails']['medium']['url'],
                    'duration': content_details['duration'],
                    'tags': snippet.get('tags', []),
                    'category_id': snippet.get('categoryId'),
                    'default_language': snippet.get('defaultLanguage'),
                    'default_audio_language': snippet.get('defaultAudioLanguage')
                }
        except Exception as e:
            logger.error(f"Error fetching video info: {e}")
            return None
    
    def get_comments(self, video_id: str, max_results: int = 1000) -> List[Dict[str, Any]]:
        """Fetch and analyze comments from a YouTube video."""
        comments = []
        next_page_token = None
        
        try:
            while len(comments) < max_results:
                request = self.youtube.commentThreads().list(
                    part="snippet,replies",
                    videoId=video_id,
                    maxResults=min(100, max_results - len(comments)),
                    pageToken=next_page_token,
                    order="relevance"  # Can be: time, relevance
                )
                
                response = request.execute()
                
                for item in response['items']:
                    comment_data = item['snippet']['topLevelComment']['snippet']
                    
                    # Enhanced comment analysis
                    comment_text = comment_data['textDisplay']
                    sentiment = self.classify_sentiment(comment_text)
                    sarcasm = self.detect_sarcasm(comment_text)
                    is_question = self.is_question(comment_text)
                    category = self.categorize_comment(comment_text)
                    
                    comment_info = {
                        "id": item['snippet']['topLevelComment']['id'],
                        "author": comment_data['authorDisplayName'],
                        "author_channel_id": comment_data.get('authorChannelId', {}).get('value'),
                        "comment": comment_text,
                        "published": comment_data['publishedAt'],
                        "updated": comment_data['updatedAt'],
                        "likes": comment_data.get('likeCount', 0),
                        "sentiment": sentiment,
                        "sarcasm": sarcasm,
                        "is_question": is_question,
                        "category": category,
                        "reply_count": item['snippet'].get('totalReplyCount', 0),
                        "is_english": self.is_english(comment_text)
                    }
                    
                    comments.append(comment_info)
                    
                    # Get replies if they exist
                    if item['snippet'].get('totalReplyCount', 0) > 0 and 'replies' in item:
                        for reply in item['replies']['comments']:
                            reply_data = reply['snippet']
                            reply_text = reply_data['textDisplay']
                            
                            reply_info = {
                                "id": reply['id'],
                                "parent_id": item['snippet']['topLevelComment']['id'],
                                "author": reply_data['authorDisplayName'],
                                "author_channel_id": reply_data.get('authorChannelId', {}).get('value'),
                                "comment": reply_text,
                                "published": reply_data['publishedAt'],
                                "updated": reply_data['updatedAt'],
                                "likes": reply_data.get('likeCount', 0),
                                "sentiment": self.classify_sentiment(reply_text),
                                "sarcasm": self.detect_sarcasm(reply_text),
                                "is_question": self.is_question(reply_text),
                                "category": self.categorize_comment(reply_text),
                                "reply_count": 0,
                                "is_english": self.is_english(reply_text)
                            }
                            
                            comments.append(reply_info)
                
                next_page_token = response.get('nextPageToken')
                if not next_page_token:
                    break
                    
        except HttpError as e:
            if e.resp.status == 403:
                logger.warning(f"Comments disabled for video {video_id}")
            else:
                logger.error(f"Error fetching comments: {e}")
        except Exception as e:
            logger.error(f"Error fetching comments: {e}")
        
        return comments
    
    def get_transcript(self, video_id: str) -> Optional[str]:
        """Get video transcript using YouTube Transcript API."""
        try:
            transcript_list = YouTubeTranscriptApi.get_transcript(video_id)
            formatter = TextFormatter()
            transcript = formatter.format_transcript(transcript_list)
            return transcript
        except Exception as e:
            logger.error(f"Error fetching transcript: {e}")
            return None
    
    def classify_sentiment(self, text: str) -> str:
        """Classify sentiment using VADER sentiment analyzer."""
        if not isinstance(text, str) or text.strip() == '':
            return 'neutral'
        
        try:
            score = self.sia.polarity_scores(text)
            compound = score['compound']
            
            if compound >= 0.05:
                return 'positive'
            elif compound <= -0.05:
                return 'negative'
            else:
                return 'neutral'
        except Exception as e:
            logger.error(f"Error classifying sentiment: {e}")
            return 'neutral'
    
    def detect_sarcasm(self, comment: str) -> str:
        """Enhanced sarcasm detection in YouTube comments."""
        if not isinstance(comment, str):
            return 'not sarcastic'
        
        text = comment.lower()
        sentiment = self.sia.polarity_scores(text)
        compound = sentiment['compound']
        
        # Check for sarcasm indicators
        contains_neg_context = any(kw in text for kw in self.negative_context_keywords)
        contains_sarcasm_clue = any(kw in text for kw in self.sarcasm_indicators)
        contains_emoji = any(e in text for e in self.emoji_indicators)
        has_caps_exaggeration = bool(re.search(r'\b[A-Z]{2,}\b', comment))
        has_quotes = '"' in comment or "'" in comment
        
        # Sarcasm detection logic
        sarcasm_score = 0
        
        if contains_neg_context:
            sarcasm_score += 2
        if contains_sarcasm_clue:
            sarcasm_score += 2
        if contains_emoji:
            sarcasm_score += 1
        if has_caps_exaggeration:
            sarcasm_score += 1
        if has_quotes:
            sarcasm_score += 1
        
        # Positive sentiment with negative context is often sarcastic
        if compound > 0.4 and (contains_neg_context or contains_sarcasm_clue):
            sarcasm_score += 2
        
        # Negative sentiment with sarcasm indicators
        if compound < 0.4 and (contains_sarcasm_clue or contains_emoji) and contains_neg_context:
            sarcasm_score += 2
        
        return 'sarcastic' if sarcasm_score >= 3 else 'not sarcastic'
    
    def is_question(self, text: str) -> bool:
        """Detect if a comment is a question."""
        if not isinstance(text, str):
            return False
        
        text_lower = text.lower()
        
        # Check for question patterns
        for pattern in self.question_patterns:
            if re.search(pattern, text_lower):
                return True
        
        return False
    
    def categorize_comment(self, text: str) -> str:
        """Categorize comment by type."""
        if not isinstance(text, str):
            return 'other'
        
        text_lower = text.lower()
        
        # Question detection
        if self.is_question(text):
            return 'question'
        
        # Appreciation
        appreciation_words = ['great', 'awesome', 'love', 'amazing', 'perfect', 'excellent', 'fantastic', 'brilliant']
        if any(word in text_lower for word in appreciation_words):
            return 'appreciation'
        
        # Criticism
        criticism_words = ['bad', 'terrible', 'awful', 'hate', 'dislike', 'worst', 'garbage', 'trash', 'boring']
        if any(word in text_lower for word in criticism_words):
            return 'criticism'
        
        # Suggestions
        suggestion_words = ['should', 'could', 'would', 'suggest', 'recommend', 'maybe', 'perhaps', 'consider']
        if any(word in text_lower for word in suggestion_words):
            return 'suggestion'
        
        # Feedback
        feedback_words = ['feedback', 'review', 'thought', 'opinion', 'think', 'feel']
        if any(word in text_lower for word in feedback_words):
            return 'feedback'
        
        # Spam detection
        spam_indicators = ['subscribe', 'like', 'comment', 'check out my channel', 'follow me']
        if any(indicator in text_lower for indicator in spam_indicators):
            return 'spam'
        
        return 'other'
    
    def is_english(self, text: str) -> bool:
        """Check if text is in English using language detection."""
        if not isinstance(text, str) or text.strip() == '':
            return False
        
        try:
            lang, confidence = langid.classify(text)
            is_ascii_heavy = len([c for c in text if ord(c) < 128]) / len(text) > 0.7
            return (lang == 'en' and confidence > 0.5) or (lang == 'en' and is_ascii_heavy)
        except:
            try:
                ascii_ratio = len([c for c in text if ord(c) < 128]) / len(text)
                return ascii_ratio > 0.8
            except:
                return False
    
    def get_channel_info(self, channel_id: str) -> Optional[Dict[str, Any]]:
        """Get channel information."""
        try:
            request = self.youtube.channels().list(
                part="snippet,statistics,brandingSettings",
                id=channel_id
            )
            response = request.execute()
            
            if response['items']:
                channel = response['items'][0]
                snippet = channel['snippet']
                statistics = channel['statistics']
                
                return {
                    'id': channel['id'],
                    'title': snippet['title'],
                    'description': snippet['description'],
                    'custom_url': snippet.get('customUrl'),
                    'published_at': snippet['publishedAt'],
                    'thumbnail': snippet['thumbnails']['medium']['url'],
                    'subscriber_count': int(statistics.get('subscriberCount', 0)),
                    'video_count': int(statistics.get('videoCount', 0)),
                    'view_count': int(statistics.get('viewCount', 0)),
                    'country': snippet.get('country'),
                    'default_language': snippet.get('defaultLanguage')
                }
        except Exception as e:
            logger.error(f"Error fetching channel info: {e}")
            return None
    
    def get_video_analytics(self, video_id: str) -> Dict[str, Any]:
        """Get comprehensive video analytics."""
        try:
            video_info = self.get_video_info(video_id)
            if not video_info:
                return {}
            
            comments = self.get_comments(video_id, max_results=500)
            
            # Calculate engagement metrics
            view_count = video_info['view_count']
            like_count = video_info['like_count']
            comment_count = video_info['comment_count']
            
            engagement_rate = (like_count + comment_count) / view_count if view_count > 0 else 0
            like_ratio = like_count / view_count if view_count > 0 else 0
            comment_ratio = comment_count / view_count if view_count > 0 else 0
            
            # Analyze comments
            sentiment_distribution = {}
            category_distribution = {}
            question_count = 0
            english_comments = 0
            
            for comment in comments:
                sentiment = comment['sentiment']
                category = comment['category']
                
                sentiment_distribution[sentiment] = sentiment_distribution.get(sentiment, 0) + 1
                category_distribution[category] = category_distribution.get(category, 0) + 1
                
                if comment['is_question']:
                    question_count += 1
                
                if comment['is_english']:
                    english_comments += 1
            
            return {
                'video_info': video_info,
                'engagement_metrics': {
                    'engagement_rate': engagement_rate,
                    'like_ratio': like_ratio,
                    'comment_ratio': comment_ratio,
                    'total_interactions': like_count + comment_count
                },
                'comment_analysis': {
                    'total_comments': len(comments),
                    'english_comments': english_comments,
                    'question_count': question_count,
                    'sentiment_distribution': sentiment_distribution,
                    'category_distribution': category_distribution,
                    'comments': comments
                }
            }
            
        except Exception as e:
            logger.error(f"Error getting video analytics: {e}")
            return {}
    
    def get_video_comments(self, video_id: str, max_results: int = 1000) -> List[Dict[str, Any]]:
        """Alias for get_comments method for compatibility with AnalyticsAgent."""
        print(f"DEBUG: get_video_comments called with video_id: {video_id}")  # Debug print
        return self.get_comments(video_id, max_results)
    
    def get_video_transcript(self, video_id: str) -> Optional[str]:
        """Get video transcript - alias for get_transcript method."""
        return self.get_transcript(video_id)

    def get_channel_videos(self, channel_id: str, max_results: int = 50) -> List[Dict[str, Any]]:
        """Get recent videos from a channel."""
        try:
            videos = []
            next_page_token = None
            
            while len(videos) < max_results:
                request = self.youtube.search().list(
                    part="id,snippet",
                    channelId=channel_id,
                    order="date",
                    type="video",
                    maxResults=min(50, max_results - len(videos)),
                    pageToken=next_page_token
                )
                
                response = request.execute()
                
                for item in response['items']:
                    video_info = {
                        'id': item['id'],
                        'title': item['snippet']['title'],
                        'description': item['snippet']['description'],
                        'published_at': item['snippet']['publishedAt'],
                        'thumbnail': item['snippet']['thumbnails']['medium']['url'],
                        'channel_title': item['snippet']['channelTitle'],
                        'channel_id': item['snippet']['channelId']
                    }
                    videos.append(video_info)
                
                next_page_token = response.get('nextPageToken')
                if not next_page_token:
                    break
                    
            return videos
            
        except Exception as e:
            logger.error(f"Error fetching channel videos: {e}")
            return []

    def search_videos_by_keywords(self, keywords: str, max_results: int = 50) -> List[Dict[str, Any]]:
        """Search for videos using keywords and return top results."""
        try:
            videos = []
            next_page_token = None
            
            while len(videos) < max_results:
                request = self.youtube.search().list(
                    part="id,snippet",
                    q=keywords,
                    type="video",
                    order="relevance",
                    maxResults=min(50, max_results - len(videos)),
                    pageToken=next_page_token
                )
                
                response = request.execute()
                
                for item in response['items']:
                    video_info = {
                        'id': item['id']['videoId'],
                        'title': item['snippet']['title'],
                        'description': item['snippet']['description'],
                        'published_at': item['snippet']['publishedAt'],
                        'thumbnail': item['snippet']['thumbnails']['medium']['url'],
                        'channel_title': item['snippet']['channelTitle'],
                        'channel_id': item['snippet']['channelId'],
                        'tags': item['snippet'].get('tags', [])
                    }
                    videos.append(video_info)
                
                next_page_token = response.get('nextPageToken')
                if not next_page_token:
                    break
                    
            return videos
            
        except Exception as e:
            logger.error(f"Error searching videos by keywords: {e}")
            return []

    def detect_sponsorships(self, transcript: str, title: str = "", description: str = "") -> Dict[str, Any]:
        """Detect sponsorships in video transcript, title, and description using LLM analysis."""
        try:
            # Use LLM-based detection as primary method
            llm_analysis = self.detect_sponsorships_with_llm(transcript, title, description)
            
            # Fallback to regex-based detection if LLM fails
            if llm_analysis.get("error") or not llm_analysis.get("has_sponsorship"):
                regex_analysis = self.detect_sponsorships_regex(transcript, title, description)
                return regex_analysis
            
            return llm_analysis
            
        except Exception as e:
            logger.error(f"Error detecting sponsorships: {e}")
            # Fallback to regex method
            return self.detect_sponsorships_regex(transcript, title, description)

    def detect_sponsorships_with_llm(self, transcript: str, title: str = "", description: str = "") -> Dict[str, Any]:
        """Detect sponsorships using LLM analysis of transcript."""
        try:
            # Try to import AI service with fallback
            try:
                from src.services.ai_service import AIService
            except ImportError:
                try:
                    from services.ai_service import AIService
                except ImportError:
                    logger.error("AIService not available, falling back to regex detection")
                    return {"error": "AIService not available"}
            
            # Check if OpenAI API key is available
            import os
            if not os.getenv('OPENAI_API_KEY'):
                logger.warning("OpenAI API key not set, falling back to regex detection")
                return {"error": "OpenAI API key not configured"}
            
            # Initialize AI service
            ai_service = AIService()
            
            # Prepare the text for analysis
            full_text = f"Title: {title}\nDescription: {description}\nTranscript: {transcript}"
            
            # Create a comprehensive prompt for sponsorship detection
            prompt = f"""
            Analyze the following YouTube video content for sponsorship indicators. Look for:

            1. **Direct Sponsorship Mentions**: "sponsored by", "partnered with", "thanks to [company]"
            2. **Product Promotions**: Mentions of specific products, services, or brands
            3. **Call-to-Action**: "check out", "visit", "use code", "link in description"
            4. **Commercial Language**: Promotional language, discount codes, affiliate links
            5. **Brand Integration**: Natural or forced brand mentions within content

            Video Content:
            {full_text[:4000]}  # Limit to avoid token limits

            Provide your analysis in the following JSON format:
            {{
                "has_sponsorship": true/false,
                "sponsorship_level": "none/low/medium/high",
                "confidence_score": 0-100,
                "sponsors": [
                    {{
                        "name": "company name",
                        "type": "direct/indirect/product_placement",
                        "confidence": 0-100,
                        "mentions": ["specific mention 1", "specific mention 2"]
                    }}
                ],
                "promotional_elements": [
                    {{
                        "type": "discount_code/call_to_action/product_mention/affiliate_link",
                        "content": "specific content",
                        "confidence": 0-100
                    }}
                ],
                "sponsorship_segments": [
                    "exact text segment 1",
                    "exact text segment 2"
                ],
                "analysis_summary": "Brief explanation of findings"
            }}

            Be thorough but accurate. Only mark as sponsorship if there are clear commercial indicators.
            """

            # Get LLM analysis
            response = ai_service.generate_content(prompt, "sponsorship detection")
            
            # Parse the response
            try:
                import json
                import re
                
                # Extract JSON from response
                json_match = re.search(r'\{.*\}', response, re.DOTALL)
                if json_match:
                    analysis = json.loads(json_match.group())
                else:
                    # Fallback parsing
                    analysis = self.parse_sponsorship_response(response)
                
                # Validate and structure the response
                return {
                    "has_sponsorship": analysis.get("has_sponsorship", False),
                    "sponsorship_level": analysis.get("sponsorship_level", "none"),
                    "confidence_score": analysis.get("confidence_score", 0),
                    "detected_indicators": [elem.get("type") for elem in analysis.get("promotional_elements", [])],
                    "detected_companies": [sponsor.get("name") for sponsor in analysis.get("sponsors", [])],
                    "extracted_companies": [sponsor.get("name") for sponsor in analysis.get("sponsors", [])],
                    "discount_codes": [elem.get("content") for elem in analysis.get("promotional_elements", []) 
                                     if elem.get("type") == "discount_code"],
                    "urls": [],  # LLM might not extract URLs reliably
                    "sponsorship_text": analysis.get("sponsorship_segments", []),
                    "llm_analysis": analysis.get("analysis_summary", ""),
                    "sponsors": analysis.get("sponsors", []),
                    "promotional_elements": analysis.get("promotional_elements", []),
                    "detection_method": "llm_analysis"
                }
                
            except (json.JSONDecodeError, KeyError) as e:
                logger.error(f"Error parsing LLM response: {e}")
                return {"error": f"Failed to parse LLM response: {str(e)}"}
                
        except Exception as e:
            logger.error(f"Error in LLM sponsorship detection: {e}")
            return {"error": f"LLM analysis failed: {str(e)}"}

    def parse_sponsorship_response(self, response: str) -> Dict[str, Any]:
        """Parse LLM response when JSON parsing fails."""
        try:
            # Extract key information using regex patterns
            has_sponsorship = bool(re.search(r'"has_sponsorship":\s*true', response, re.IGNORECASE))
            
            # Extract sponsorship level
            level_match = re.search(r'"sponsorship_level":\s*"([^"]+)"', response, re.IGNORECASE)
            sponsorship_level = level_match.group(1) if level_match else "none"
            
            # Extract confidence score
            confidence_match = re.search(r'"confidence_score":\s*(\d+)', response)
            confidence_score = int(confidence_match.group(1)) if confidence_match else 0
            
            # Extract company names
            companies = re.findall(r'"name":\s*"([^"]+)"', response)
            
            # Extract promotional elements
            promotional_types = re.findall(r'"type":\s*"([^"]+)"', response)
            
            return {
                "has_sponsorship": has_sponsorship,
                "sponsorship_level": sponsorship_level,
                "confidence_score": confidence_score,
                "sponsors": [{"name": company, "type": "detected", "confidence": 70} for company in companies],
                "promotional_elements": [{"type": ptype, "content": "", "confidence": 60} for ptype in promotional_types],
                "sponsorship_segments": [],
                "analysis_summary": "Parsed from LLM response"
            }
            
        except Exception as e:
            logger.error(f"Error parsing sponsorship response: {e}")
            return {
                "has_sponsorship": False,
                "sponsorship_level": "none",
                "confidence_score": 0,
                "sponsors": [],
                "promotional_elements": [],
                "sponsorship_segments": [],
                "analysis_summary": "Failed to parse response"
            }

    def detect_sponsorships_regex(self, transcript: str, title: str = "", description: str = "") -> Dict[str, Any]:
        """Fallback regex-based sponsorship detection (original method)."""
        try:
            # Common sponsorship indicators
            sponsorship_indicators = [
                r'sponsored\s+by',
                r'this\s+video\s+is\s+sponsored\s+by',
                r'thanks\s+to\s+.*?\s+for\s+sponsoring',
                r'partnered\s+with',
                r'in\s+partnership\s+with',
                r'promotion\s+code',
                r'discount\s+code',
                r'use\s+code\s+[A-Z0-9]+',
                r'promo\s+code',
                r'coupon\s+code',
                r'check\s+out\s+.*?\s+link\s+in\s+description',
                r'link\s+in\s+description',
                r'click\s+the\s+link\s+below',
                r'visit\s+.*?\s+com',
                r'go\s+to\s+.*?\s+com',
                r'head\s+over\s+to',
                r'check\s+out\s+.*?\s+website',
                r'visit\s+.*?\s+website',
                r'go\s+to\s+.*?\s+website',
                r'click\s+the\s+link\s+in\s+the\s+description',
                r'segway\s+9',
            ]
            
            # Common sponsorship companies and brands
            sponsorship_companies = [
                'nordvpn', 'expressvpn', 'surfshark', 'protonvpn', 'cyberghost',
                'skillshare', 'masterclass', 'udemy', 'coursera', 'brilliant',
                'audible', 'spotify', 'amazon', 'shopify', 'squarespace',
                'wix', 'bluehost', 'hostinger', 'godaddy', 'namecheap',
                'grammarly', 'honey', 'raid', 'mobile legends', 'genshin impact',
                'raycon', 'airpods', 'samsung', 'apple', 'google',
                'microsoft', 'adobe', 'canva', 'figma', 'notion',
                'robinhood', 'coinbase', 'binance', 'stripe', 'paypal',
                'uber', 'lyft', 'doordash', 'ubereats', 'grubhub',
                'netflix', 'disney+', 'hulu', 'hbo max', 'paramount+',
                'nike', 'adidas', 'puma', 'under armour', 'reebok',
                'coca cola', 'pepsi', 'red bull', 'monster', 'gatorade',
                'mcdonalds', 'burger king', 'kfc', 'subway', 'dominos',
                'starbucks', 'dunkin', 'tim hortons', 'peets', 'caribou'
            ]
            
            # Combine all text for analysis
            full_text = f"{title} {description} {transcript}".lower()
            
            # Detect sponsorship indicators
            detected_indicators = []
            for pattern in sponsorship_indicators:
                matches = re.findall(pattern, full_text, re.IGNORECASE)
                if matches:
                    detected_indicators.extend(matches)
            
            # Detect sponsorship companies
            detected_companies = []
            for company in sponsorship_companies:
                if company.lower() in full_text:
                    detected_companies.append(company)
            
            # Extract potential company names using regex patterns
            company_patterns = [
                r'(?:sponsored by|partnered with|thanks to)\s+([A-Z][a-zA-Z\s&]+?)(?:\s+for|\.|,|$)',
                r'check out ([A-Z][a-zA-Z\s&]+?)(?:\s+at|\.|,|$)',
                r'visit ([A-Z][a-zA-Z\s&]+?)(?:\s+com|\.|,|$)',
                r'go to ([A-Z][a-zA-Z\s&]+?)(?:\s+com|\.|,|$)',
                r'head over to ([A-Z][a-zA-Z\s&]+?)(?:\s+com|\.|,|$)'
            ]
            
            extracted_companies = []
            for pattern in company_patterns:
                matches = re.findall(pattern, full_text, re.IGNORECASE)
                extracted_companies.extend([match.strip() for match in matches])
            
            # Extract discount codes
            discount_codes = re.findall(r'(?:use|promo|discount|coupon)\s+code\s+([A-Z0-9]+)', full_text, re.IGNORECASE)
            
            # Extract URLs
            urls = re.findall(r'https?://[^\s]+', full_text)
            
            # Determine sponsorship confidence
            confidence_score = 0
            if detected_indicators:
                confidence_score += 30
            if detected_companies:
                confidence_score += 25
            if extracted_companies:
                confidence_score += 20
            if discount_codes:
                confidence_score += 15
            if urls:
                confidence_score += 10
            
            sponsorship_level = "none"
            if confidence_score >= 70:
                sponsorship_level = "high"
            elif confidence_score >= 40:
                sponsorship_level = "medium"
            elif confidence_score >= 20:
                sponsorship_level = "low"
            
            return {
                "has_sponsorship": confidence_score >= 20,
                "sponsorship_level": sponsorship_level,
                "confidence_score": confidence_score,
                "detected_indicators": list(set(detected_indicators)),
                "detected_companies": list(set(detected_companies)),
                "extracted_companies": list(set(extracted_companies)),
                "discount_codes": list(set(discount_codes)),
                "urls": list(set(urls)),
                "sponsorship_text": self.extract_sponsorship_text(transcript, title, description),
                "detection_method": "regex_fallback"
            }
            
        except Exception as e:
            logger.error(f"Error detecting sponsorships: {e}")
            return {
                "has_sponsorship": False,
                "sponsorship_level": "none",
                "confidence_score": 0,
                "detected_indicators": [],
                "detected_companies": [],
                "extracted_companies": [],
                "discount_codes": [],
                "urls": [],
                "sponsorship_text": [],
                "detection_method": "regex_fallback"
            }

    def extract_sponsorship_text(self, transcript: str, title: str = "", description: str = "") -> List[str]:
        """Extract specific text segments that indicate sponsorship."""
        try:
            sponsorship_segments = []
            full_text = f"{title} {description} {transcript}"
            
            # Patterns to extract sponsorship segments
            patterns = [
                r'[^.]*(?:sponsored by|partnered with|thanks to)[^.]*\.',
                r'[^.]*(?:check out|visit|go to|head over to)[^.]*\.',
                r'[^.]*(?:use code|promo code|discount code)[^.]*\.',
                r'[^.]*(?:link in description|click the link)[^.]*\.'
            ]
            
            for pattern in patterns:
                matches = re.findall(pattern, full_text, re.IGNORECASE)
                sponsorship_segments.extend(matches)
            
            return list(set(sponsorship_segments))
            
        except Exception as e:
            logger.error(f"Error extracting sponsorship text: {e}")
            return []

    def get_video_tags(self, video_id: str) -> List[str]:
        """Get video tags from YouTube API."""
        try:
            request = self.youtube.videos().list(
                part="snippet",
                id=video_id
            )
            response = request.execute()
            
            if response['items']:
                return response['items'][0]['snippet'].get('tags', [])
            return []
            
        except Exception as e:
            logger.error(f"Error fetching video tags: {e}")
            return []

    def compare_videos_by_keywords(self, video_id: str, max_results: int = 5) -> Dict[str, Any]:
        """Compare a video with similar videos based on keywords/tags, excluding same channel."""
        try:
            # Get the original video info
            original_video = self.get_video_info(video_id)
            if not original_video:
                return {"error": "Original video not found"}
            
            # Get video tags
            video_tags = self.get_video_tags(video_id)
            
            # Create search keywords from title and tags, excluding channel name
            search_keywords = original_video['title']
            if video_tags:
                search_keywords += " " + " ".join(video_tags[:5])  # Use top 5 tags
            
            # Remove channel name from search keywords to avoid bias
            channel_name = original_video.get('channel', '').lower()
            if channel_name:
                # Remove channel name and common variations from search keywords
                search_keywords_lower = search_keywords.lower()
                channel_variations = [
                    channel_name,
                    channel_name.replace(' ', ''),
                    channel_name.replace(' ', '_'),
                    channel_name.replace(' ', '-'),
                    # Add individual words from channel name
                    *channel_name.split()
                ]
                
                # Filter out channel name variations
                words = search_keywords.split()
                filtered_words = []
                for word in words:
                    word_lower = word.lower()
                    # Skip if word contains any channel variation
                    if not any(variation in word_lower or word_lower in variation for variation in channel_variations):
                        filtered_words.append(word)
                
                search_keywords = ' '.join(filtered_words)
            
            # Search for similar videos
            similar_videos = self.search_videos_by_keywords(search_keywords, max_results * 3)  # Get more to filter
            
            # Analyze each similar video, excluding same channel
            analyzed_videos = []
            original_channel_id = original_video.get('channel_id', '')
            
            for video in similar_videos:
                if video['id'] == video_id:  # Skip the original video
                    continue
                
                # Skip videos from the same channel
                if video.get('channel_id') == original_channel_id:
                    continue
                
                # Get detailed video info
                video_details = self.get_video_info(video['id'])
                if not video_details:
                    continue
                
                # Double-check channel ID to ensure we're not getting same channel
                if video_details.get('channel_id') == original_channel_id:
                    continue
                
                # Get transcript
                transcript = self.get_transcript(video['id'])
                
                # Detect sponsorships
                sponsorship_analysis = self.detect_sponsorships(
                    transcript or "",
                    video_details.get('title', ""),
                    video_details.get('description', "")
                )
                
                # Calculate engagement metrics
                view_count = video_details.get('view_count', 0)
                like_count = video_details.get('like_count', 0)
                comment_count = video_details.get('comment_count', 0)
                engagement_rate = ((like_count + comment_count) / view_count * 100) if view_count > 0 else 0
                
                analyzed_video = {
                    "video_id": video['id'],
                    "title": video_details.get('title', ''),
                    "channel": video_details.get('channel', ''),
                    "channel_id": video_details.get('channel_id', ''),
                    "published_at": video_details.get('published_at', ''),
                    "view_count": view_count,
                    "like_count": like_count,
                    "comment_count": comment_count,
                    "engagement_rate": round(engagement_rate, 2),
                    "duration": video_details.get('duration', ''),
                    "tags": video_details.get('tags', []),
                    "transcript_length": len(transcript) if transcript else 0,
                    "sponsorship_analysis": sponsorship_analysis,
                    "thumbnail": video_details.get('thumbnail', '')
                }
                
                analyzed_videos.append(analyzed_video)
                
                # Stop if we have enough videos from different channels
                if len(analyzed_videos) >= max_results:
                    break
            
            # Sort by relevance (engagement rate and view count)
            analyzed_videos.sort(key=lambda x: (x['engagement_rate'], x['view_count']), reverse=True)
            
            # Generate technical insights
            technical_insights = self.analyze_technical_insights(original_video, analyzed_videos)
            
            return {
                "original_video": {
                    "video_id": video_id,
                    "title": original_video.get('title', ''),
                    "channel": original_video.get('channel', ''),
                    "channel_id": original_video.get('channel_id', ''),
                    "view_count": original_video.get('view_count', 0),
                    "like_count": original_video.get('like_count', 0),
                    "comment_count": original_video.get('comment_count', 0),
                    "tags": video_tags
                },
                "search_keywords": search_keywords,
                "similar_videos": analyzed_videos[:max_results],
                "total_found": len(analyzed_videos),
                "excluded_same_channel": True,
                "sponsorship_summary": self.generate_sponsorship_summary(analyzed_videos),
                "technical_insights": technical_insights
            }
            
        except Exception as e:
            logger.error(f"Error comparing videos by keywords: {e}")
            return {"error": str(e)}

    def generate_sponsorship_summary(self, videos: List[Dict]) -> Dict[str, Any]:
        """Generate a summary of sponsorship patterns across videos."""
        try:
            total_videos = len(videos)
            sponsored_videos = [v for v in videos if v.get('sponsorship_analysis', {}).get('has_sponsorship', False)]
            
            # Collect all sponsorship companies
            all_companies = []
            all_codes = []
            sponsorship_levels = {"high": 0, "medium": 0, "low": 0, "none": 0}
            
            for video in videos:
                sponsorship = video.get('sponsorship_analysis', {})
                all_companies.extend(sponsorship.get('detected_companies', []))
                all_companies.extend(sponsorship.get('extracted_companies', []))
                all_codes.extend(sponsorship.get('discount_codes', []))
                
                level = sponsorship.get('sponsorship_level', 'none')
                sponsorship_levels[level] += 1
            
            # Count company frequency
            company_frequency = {}
            for company in all_companies:
                company_frequency[company] = company_frequency.get(company, 0) + 1
            
            return {
                "total_videos": total_videos,
                "sponsored_videos": len(sponsored_videos),
                "sponsorship_rate": round((len(sponsored_videos) / total_videos * 100), 2) if total_videos > 0 else 0,
                "sponsorship_levels": sponsorship_levels,
                "top_sponsors": sorted(company_frequency.items(), key=lambda x: x[1], reverse=True)[:10],
                "discount_codes": list(set(all_codes)),
                "common_sponsorship_indicators": self.get_common_sponsorship_indicators(videos)
            }
            
        except Exception as e:
            logger.error(f"Error generating sponsorship summary: {e}")
            return {}

    def get_common_sponsorship_indicators(self, videos: List[Dict]) -> List[str]:
        """Get common sponsorship indicators across videos."""
        try:
            all_indicators = []
            for video in videos:
                sponsorship = video.get('sponsorship_analysis', {})
                all_indicators.extend(sponsorship.get('detected_indicators', []))
            
            # Count frequency
            indicator_frequency = {}
            for indicator in all_indicators:
                indicator_frequency[indicator] = indicator_frequency.get(indicator, 0) + 1
            
            # Return top indicators
            return sorted(indicator_frequency.items(), key=lambda x: x[1], reverse=True)[:5]
            
        except Exception as e:
            logger.error(f"Error getting common sponsorship indicators: {e}")
            return []

    def analyze_technical_insights(self, original_video: Dict, similar_videos: List[Dict]) -> Dict[str, Any]:
        """Analyze technical insights and success patterns for videos."""
        try:
            insights = {
                "original_video_analysis": {},
                "similar_videos_analysis": {},
                "success_patterns": {},
                "engagement_strategies": {},
                "content_insights": {},
                "recommendations": []
            }
            
            # Analyze original video
            original_views = original_video.get('view_count', 0)
            original_likes = original_video.get('like_count', 0)
            original_comments = original_video.get('comment_count', 0)
            original_engagement_rate = ((original_likes + original_comments) / original_views * 100) if original_views > 0 else 0
            original_like_ratio = (original_likes / original_views * 100) if original_views > 0 else 0
            original_comment_ratio = (original_comments / original_views * 100) if original_views > 0 else 0
            
            insights["original_video_analysis"] = {
                "engagement_rate": round(original_engagement_rate, 2),
                "like_ratio": round(original_like_ratio, 2),
                "comment_ratio": round(original_comment_ratio, 2),
                "total_engagement": original_likes + original_comments,
                "engagement_per_view": round((original_likes + original_comments) / original_views, 4) if original_views > 0 else 0
            }
            
            # Analyze similar videos patterns
            if similar_videos:
                # Calculate averages
                avg_engagement_rate = sum(v.get('engagement_rate', 0) for v in similar_videos) / len(similar_videos)
                avg_like_ratio = sum((v.get('like_count', 0) / v.get('view_count', 1) * 100) for v in similar_videos) / len(similar_videos)
                avg_comment_ratio = sum((v.get('comment_count', 0) / v.get('view_count', 1) * 100) for v in similar_videos) / len(similar_videos)
                
                # Find top performers
                top_engagement = max(similar_videos, key=lambda x: x.get('engagement_rate', 0))
                top_likes = max(similar_videos, key=lambda x: x.get('like_count', 0))
                top_comments = max(similar_videos, key=lambda x: x.get('comment_count', 0))
                
                # Analyze content patterns
                title_patterns = self.analyze_title_patterns([original_video] + similar_videos)
                tag_patterns = self.analyze_tag_patterns([original_video] + similar_videos)
                duration_patterns = self.analyze_duration_patterns([original_video] + similar_videos)
                sponsorship_patterns = self.analyze_sponsorship_patterns(similar_videos)
                
                insights["similar_videos_analysis"] = {
                    "average_metrics": {
                        "engagement_rate": round(avg_engagement_rate, 2),
                        "like_ratio": round(avg_like_ratio, 2),
                        "comment_ratio": round(avg_comment_ratio, 2)
                    },
                    "top_performers": {
                        "highest_engagement": {
                            "title": top_engagement.get('title', ''),
                            "engagement_rate": top_engagement.get('engagement_rate', 0),
                            "channel": top_engagement.get('channel', ''),
                            "views": top_engagement.get('view_count', 0)
                        },
                        "most_likes": {
                            "title": top_likes.get('title', ''),
                            "likes": top_likes.get('like_count', 0),
                            "channel": top_likes.get('channel', ''),
                            "views": top_likes.get('view_count', 0)
                        },
                        "most_comments": {
                            "title": top_comments.get('title', ''),
                            "comments": top_comments.get('comment_count', 0),
                            "channel": top_comments.get('channel', ''),
                            "views": top_comments.get('view_count', 0)
                        }
                    },
                    "content_patterns": {
                        "title_patterns": title_patterns,
                        "tag_patterns": tag_patterns,
                        "duration_patterns": duration_patterns,
                        "sponsorship_patterns": sponsorship_patterns
                    }
                }
                
                # Generate success patterns
                insights["success_patterns"] = self.identify_success_patterns(original_video, similar_videos)
                
                # Analyze engagement strategies
                insights["engagement_strategies"] = self.analyze_engagement_strategies(original_video, similar_videos)
                
                # Generate content insights
                insights["content_insights"] = self.generate_content_insights(original_video, similar_videos)
                
                # Generate recommendations
                insights["recommendations"] = self.generate_recommendations(original_video, similar_videos)
            
            return insights
            
        except Exception as e:
            logger.error(f"Error analyzing technical insights: {e}")
            return {"error": str(e)}

    def analyze_title_patterns(self, videos: List[Dict]) -> Dict[str, Any]:
        """Analyze title patterns that correlate with success."""
        try:
            patterns = {
                "common_keywords": {},
                "title_length": [],
                "has_emojis": 0,
                "has_numbers": 0,
                "has_brackets": 0,
                "has_quotes": 0
            }
            
            for video in videos:
                title = video.get('title', '').lower()
                engagement_rate = video.get('engagement_rate', 0)
                
                # Title length
                patterns["title_length"].append(len(title))
                
                # Check for patterns
                if any(emoji in title for emoji in ['🎵', '🎶', '🎤', '🎧', '🎼', '🔥', '💯', '⭐', '✨']):
                    patterns["has_emojis"] += 1
                if any(char.isdigit() for char in title):
                    patterns["has_numbers"] += 1
                if '[' in title or ']' in title or '(' in title or ')' in title:
                    patterns["has_brackets"] += 1
                if '"' in title or "'" in title:
                    patterns["has_quotes"] += 1
                
                # Extract keywords
                words = title.split()
                for word in words:
                    if len(word) > 2:  # Skip short words
                        patterns["common_keywords"][word] = patterns["common_keywords"].get(word, 0) + 1
            
            # Sort keywords by frequency
            patterns["common_keywords"] = dict(sorted(patterns["common_keywords"].items(), key=lambda x: x[1], reverse=True)[:10])
            
            return patterns
            
        except Exception as e:
            logger.error(f"Error analyzing title patterns: {e}")
            return {}

    def analyze_tag_patterns(self, videos: List[Dict]) -> Dict[str, Any]:
        """Analyze tag patterns that correlate with success."""
        try:
            patterns = {
                "most_common_tags": {},
                "tag_count": [],
                "engagement_by_tag_count": {},
                "successful_tag_combinations": []
            }
            
            for video in videos:
                tags = video.get('tags', [])
                engagement_rate = video.get('engagement_rate', 0)
                
                # Tag count
                patterns["tag_count"].append(len(tags))
                
                # Count tag frequency
                for tag in tags:
                    patterns["most_common_tags"][tag] = patterns["most_common_tags"].get(tag, 0) + 1
                
                # Analyze successful tag combinations
                if engagement_rate > 3:  # High engagement threshold
                    patterns["successful_tag_combinations"].append({
                        "tags": tags[:5],  # Top 5 tags
                        "engagement_rate": engagement_rate,
                        "title": video.get('title', '')
                    })
            
            # Sort tags by frequency
            patterns["most_common_tags"] = dict(sorted(patterns["most_common_tags"].items(), key=lambda x: x[1], reverse=True)[:15])
            
            return patterns
            
        except Exception as e:
            logger.error(f"Error analyzing tag patterns: {e}")
            return {}

    def analyze_duration_patterns(self, videos: List[Dict]) -> Dict[str, Any]:
        """Analyze duration patterns and their impact on engagement."""
        try:
            patterns = {
                "duration_ranges": {
                    "short": {"count": 0, "avg_engagement": 0, "videos": []},  # 0-5 min
                    "medium": {"count": 0, "avg_engagement": 0, "videos": []},  # 5-15 min
                    "long": {"count": 0, "avg_engagement": 0, "videos": []}     # 15+ min
                },
                "optimal_duration": None
            }
            
            for video in videos:
                duration_str = video.get('duration', 'PT0S')
                duration_seconds = self.parse_duration(duration_str)
                engagement_rate = video.get('engagement_rate', 0)
                
                if duration_seconds <= 300:  # 5 minutes
                    patterns["duration_ranges"]["short"]["count"] += 1
                    patterns["duration_ranges"]["short"]["videos"].append({
                        "title": video.get('title', ''),
                        "duration": duration_seconds,
                        "engagement": engagement_rate
                    })
                elif duration_seconds <= 900:  # 15 minutes
                    patterns["duration_ranges"]["medium"]["count"] += 1
                    patterns["duration_ranges"]["medium"]["videos"].append({
                        "title": video.get('title', ''),
                        "duration": duration_seconds,
                        "engagement": engagement_rate
                    })
                else:
                    patterns["duration_ranges"]["long"]["count"] += 1
                    patterns["duration_ranges"]["long"]["videos"].append({
                        "title": video.get('title', ''),
                        "duration": duration_seconds,
                        "engagement": engagement_rate
                    })
            
            # Calculate average engagement for each duration range
            for range_name, range_data in patterns["duration_ranges"].items():
                if range_data["videos"]:
                    avg_engagement = sum(v["engagement"] for v in range_data["videos"]) / len(range_data["videos"])
                    range_data["avg_engagement"] = round(avg_engagement, 2)
            
            # Find optimal duration
            best_range = max(patterns["duration_ranges"].items(), key=lambda x: x[1]["avg_engagement"])
            patterns["optimal_duration"] = best_range[0]
            
            return patterns
            
        except Exception as e:
            logger.error(f"Error analyzing duration patterns: {e}")
            return {}

    def parse_duration(self, duration_str: str) -> int:
        """Parse ISO 8601 duration string to seconds."""
        try:
            import re
            # Parse PT1H2M3S format
            match = re.match(r'PT(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?', duration_str)
            if match:
                hours = int(match.group(1) or 0)
                minutes = int(match.group(2) or 0)
                seconds = int(match.group(3) or 0)
                return hours * 3600 + minutes * 60 + seconds
            return 0
        except:
            return 0

    def analyze_sponsorship_patterns(self, videos: List[Dict]) -> Dict[str, Any]:
        """Analyze sponsorship patterns and their impact on engagement."""
        try:
            patterns = {
                "sponsored_videos": [],
                "non_sponsored_videos": [],
                "sponsorship_impact": {
                    "sponsored_avg_engagement": 0,
                    "non_sponsored_avg_engagement": 0,
                    "engagement_difference": 0
                },
                "top_sponsors": {},
                "sponsorship_timing": []
            }
            
            for video in videos:
                sponsorship = video.get('sponsorship_analysis', {})
                engagement_rate = video.get('engagement_rate', 0)
                
                if sponsorship.get('has_sponsorship', False):
                    patterns["sponsored_videos"].append({
                        "title": video.get('title', ''),
                        "engagement": engagement_rate,
                        "sponsors": sponsorship.get('detected_companies', [])
                    })
                    
                    # Count sponsors
                    for sponsor in sponsorship.get('detected_companies', []):
                        patterns["top_sponsors"][sponsor] = patterns["top_sponsors"].get(sponsor, 0) + 1
                else:
                    patterns["non_sponsored_videos"].append({
                        "title": video.get('title', ''),
                        "engagement": engagement_rate
                    })
            
            # Calculate sponsorship impact
            if patterns["sponsored_videos"]:
                sponsored_avg = sum(v["engagement"] for v in patterns["sponsored_videos"]) / len(patterns["sponsored_videos"])
                patterns["sponsorship_impact"]["sponsored_avg_engagement"] = round(sponsored_avg, 2)
            
            if patterns["non_sponsored_videos"]:
                non_sponsored_avg = sum(v["engagement"] for v in patterns["non_sponsored_videos"]) / len(patterns["non_sponsored_videos"])
                patterns["sponsorship_impact"]["non_sponsored_avg_engagement"] = round(non_sponsored_avg, 2)
            
            if patterns["sponsorship_impact"]["sponsored_avg_engagement"] > 0 and patterns["sponsorship_impact"]["non_sponsored_avg_engagement"] > 0:
                diff = patterns["sponsorship_impact"]["sponsored_avg_engagement"] - patterns["sponsorship_impact"]["non_sponsored_avg_engagement"]
                patterns["sponsorship_impact"]["engagement_difference"] = round(diff, 2)
            
            # Sort top sponsors
            patterns["top_sponsors"] = dict(sorted(patterns["top_sponsors"].items(), key=lambda x: x[1], reverse=True)[:5])
            
            return patterns
            
        except Exception as e:
            logger.error(f"Error analyzing sponsorship patterns: {e}")
            return {}

    def identify_success_patterns(self, original_video: Dict, similar_videos: List[Dict]) -> Dict[str, Any]:
        """Identify patterns that correlate with video success."""
        try:
            patterns = {
                "high_engagement_indicators": [],
                "content_strategies": [],
                "timing_factors": [],
                "audience_behavior": []
            }
            
            # Analyze high engagement videos
            high_engagement_videos = [v for v in similar_videos if v.get('engagement_rate', 0) > 3]
            
            if high_engagement_videos:
                # Common characteristics of high engagement videos
                for video in high_engagement_videos:
                    title = video.get('title', '').lower()
                    
                    # Content strategies
                    if any(word in title for word in ['lyrics', 'lyric', 'karaoke']):
                        patterns["content_strategies"].append("Lyrics/karaoke content performs well")
                    if any(word in title for word in ['official', 'original']):
                        patterns["content_strategies"].append("Official/original content gets higher engagement")
                    if any(word in title for word in ['remix', 'cover', 'version']):
                        patterns["content_strategies"].append("Remixes and covers attract engagement")
                    
                    # Engagement indicators
                    if video.get('comment_count', 0) > video.get('like_count', 0) * 0.1:
                        patterns["high_engagement_indicators"].append("High comment-to-like ratio indicates strong community engagement")
                    
                    # Audience behavior
                    if video.get('view_count', 0) < 1000000 and video.get('engagement_rate', 0) > 5:
                        patterns["audience_behavior"].append("Smaller channels often have higher engagement rates")
            
            # Remove duplicates
            for key in patterns:
                patterns[key] = list(set(patterns[key]))
            
            return patterns
            
        except Exception as e:
            logger.error(f"Error identifying success patterns: {e}")
            return {}

    def analyze_engagement_strategies(self, original_video: Dict, similar_videos: List[Dict]) -> Dict[str, Any]:
        """Analyze engagement strategies used by successful videos."""
        try:
            strategies = {
                "title_optimization": [],
                "content_approaches": [],
                "community_engagement": [],
                "timing_strategies": []
            }
            
            # Analyze top performing videos
            top_videos = sorted(similar_videos, key=lambda x: x.get('engagement_rate', 0), reverse=True)[:3]
            
            for video in top_videos:
                title = video.get('title', '')
                engagement_rate = video.get('engagement_rate', 0)
                
                # Title optimization strategies
                if '[' in title or ']' in title:
                    strategies["title_optimization"].append("Bracketed titles help with discoverability")
                if any(emoji in title for emoji in ['🎵', '🎶', '🎤', '🔥']):
                    strategies["title_optimization"].append("Emojis in titles increase click-through rates")
                if len(title) > 50:
                    strategies["title_optimization"].append("Longer titles provide more context")
                
                # Content approaches
                if 'lyrics' in title.lower():
                    strategies["content_approaches"].append("Lyrics content drives high engagement")
                if 'official' in title.lower():
                    strategies["content_approaches"].append("Official content builds trust")
                if 'remix' in title.lower() or 'cover' in title.lower():
                    strategies["content_approaches"].append("Remixes and covers attract diverse audiences")
                
                # Community engagement
                comment_ratio = (video.get('comment_count', 0) / video.get('view_count', 1)) * 100
                if comment_ratio > 0.1:
                    strategies["community_engagement"].append("High comment ratios indicate strong community interaction")
                
                # Timing strategies
                if video.get('view_count', 0) < 500000 and engagement_rate > 4:
                    strategies["timing_strategies"].append("Niche content timing can lead to higher engagement")
            
            # Remove duplicates
            for key in strategies:
                strategies[key] = list(set(strategies[key]))
            
            return strategies
            
        except Exception as e:
            logger.error(f"Error analyzing engagement strategies: {e}")
            return {}

    def generate_content_insights(self, original_video: Dict, similar_videos: List[Dict]) -> Dict[str, Any]:
        """Generate insights about content performance and optimization."""
        try:
            insights = {
                "performance_comparison": {},
                "optimization_opportunities": [],
                "content_gaps": [],
                "trending_elements": []
            }
            
            # Compare original video performance with similar videos
            original_engagement = ((original_video.get('like_count', 0) + original_video.get('comment_count', 0)) / original_video.get('view_count', 1)) * 100
            avg_similar_engagement = sum(v.get('engagement_rate', 0) for v in similar_videos) / len(similar_videos) if similar_videos else 0
            
            insights["performance_comparison"] = {
                "original_engagement_rate": round(original_engagement, 2),
                "average_similar_engagement": round(avg_similar_engagement, 2),
                "performance_percentile": self.calculate_percentile(original_engagement, [v.get('engagement_rate', 0) for v in similar_videos]),
                "relative_performance": "above_average" if original_engagement > avg_similar_engagement else "below_average"
            }
            
            # Identify optimization opportunities
            if original_engagement < avg_similar_engagement:
                insights["optimization_opportunities"].append("Consider adding more engaging elements to increase interaction")
                insights["optimization_opportunities"].append("Review title and thumbnail optimization based on successful similar videos")
                insights["optimization_opportunities"].append("Analyze timing and posting schedule of high-performing videos")
            
            # Identify content gaps
            successful_tags = set()
            for video in similar_videos:
                if video.get('engagement_rate', 0) > 3:
                    successful_tags.update(video.get('tags', []))
            
            original_tags = set(original_video.get('tags', []))
            missing_tags = successful_tags - original_tags
            if missing_tags:
                insights["content_gaps"].append(f"Consider adding these trending tags: {', '.join(list(missing_tags)[:5])}")
            
            # Identify trending elements
            trending_keywords = []
            for video in similar_videos:
                if video.get('engagement_rate', 0) > 4:
                    title_words = video.get('title', '').lower().split()
                    trending_keywords.extend([word for word in title_words if len(word) > 3])
            
            if trending_keywords:
                from collections import Counter
                most_common = Counter(trending_keywords).most_common(5)
                insights["trending_elements"].append(f"Trending keywords in successful videos: {', '.join([word for word, count in most_common])}")
            
            return insights
            
        except Exception as e:
            logger.error(f"Error generating content insights: {e}")
            return {}

    def calculate_percentile(self, value: float, values: List[float]) -> int:
        """Calculate percentile of a value in a list of values."""
        try:
            if not values:
                return 50
            sorted_values = sorted(values)
            position = 0
            for i, v in enumerate(sorted_values):
                if value <= v:
                    position = i
                    break
            else:
                position = len(sorted_values)
            
            percentile = (position / len(sorted_values)) * 100
            return round(percentile)
        except:
            return 50

    def generate_recommendations(self, original_video: Dict, similar_videos: List[Dict]) -> List[str]:
        """Generate actionable recommendations based on analysis."""
        try:
            recommendations = []
            
            # Analyze original video performance
            original_engagement = ((original_video.get('like_count', 0) + original_video.get('comment_count', 0)) / original_video.get('view_count', 1)) * 100
            avg_similar_engagement = sum(v.get('engagement_rate', 0) for v in similar_videos) / len(similar_videos) if similar_videos else 0
            
            # Performance-based recommendations
            if original_engagement < avg_similar_engagement:
                recommendations.append("📈 Focus on increasing engagement through community interaction and calls-to-action")
                recommendations.append("🎯 Optimize title and thumbnail based on successful similar videos")
                recommendations.append("⏰ Analyze posting timing of high-performing videos in your niche")
            
            # Content strategy recommendations
            high_engagement_videos = [v for v in similar_videos if v.get('engagement_rate', 0) > 3]
            if high_engagement_videos:
                recommendations.append("🎵 Consider creating lyrics or karaoke content which shows high engagement")
                recommendations.append("🏷️ Use trending tags from successful similar videos")
                recommendations.append("💬 Encourage comments by asking questions or creating discussion points")
            
            # Technical recommendations
            recommendations.append("📊 Monitor engagement patterns and adjust content strategy accordingly")
            recommendations.append("🔍 Research trending keywords and incorporate them naturally")
            recommendations.append("🤝 Engage with your audience through comments and community posts")
            
            return recommendations[:8]  # Limit to top 8 recommendations
            
        except Exception as e:
            logger.error(f"Error generating recommendations: {e}")
            return ["Analyze your content performance and optimize based on successful similar videos"] 