"""
YouTube Analytics AI Backend API
"""

import os
import logging
import re
import json
import numpy as np
import pandas as pd
import base64
import io
import matplotlib
matplotlib.use('Agg')  # Use non-interactive backend
import matplotlib.pyplot as plt
import seaborn as sns
from urllib.parse import urlparse, parse_qs
from googleapiclient.discovery import build
import nltk
from nltk.sentiment import SentimentIntensityAnalyzer
import langid
from datetime import datetime
from flask import Flask, request, jsonify
from flask_cors import CORS
from openai import OpenAI
try:
    from src.agents.analytics_agent import AnalyticsAgent
except ImportError:
    from agents.analytics_agent import AnalyticsAgent
from dotenv import load_dotenv

load_dotenv()

# Create Flask app
app = Flask(__name__)

# Configure CORS after app is created
CORS(app, resources={
    r"/api/*": {
        "origins": ["http://localhost:3000", "http://127.0.0.1:3000", "http://localhost:5173"],
        "methods": ["GET", "POST", "OPTIONS"],
        "allow_headers": ["Content-Type", "Authorization"]
    }
})

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Initialize OpenAI client
client = OpenAI()

class YouTubeCommentAnalyzer:
    def __init__(self, api_key):
        """Initialize the analyzer with YouTube API key."""
        self.api_key = api_key
        self.youtube = build("youtube", "v3", developerKey=api_key)
        self.sia = SentimentIntensityAnalyzer()
        
        # Define sarcasm detection keywords and indicators
        self.negative_context_keywords = [
            'clickbait', 'waste of time', 'fake', 'scam', 'slow', 'unwatchable', 
            'ads', 'noise', 'laggy', 'again', 'great job', 'thanks a lot', 
            'as always', 'fell off'
        ]
        
        self.sarcasm_indicators = [
            'sure', 'totally', 'wow', 'thanks a lot', 'great job', 'genius',
            'lmao', 'lol', 'yeah right', 'can\'t wait', 'so helpful', 'this aged well'
        ]
        
        self.emoji_indicators = ['🙄', '😒', '🤡', '😂', '🤣', '😑']

    def extract_video_id(self, url):
        """Extract video ID from YouTube URL."""
        parsed_url = urlparse(url)
        
        # Handle youtu.be short links
        if parsed_url.hostname in ["youtu.be"]:
            return parsed_url.path.lstrip("/")
        
        # Handle full length YouTube links
        elif parsed_url.hostname in ["www.youtube.com", "youtube.com"]:
            query = parse_qs(parsed_url.query)
            return query.get("v", [None])[0]
        
        return None

    def get_video_info(self, video_id):
        """Get video metadata."""
        try:
            request = self.youtube.videos().list(
                part="snippet,statistics",
                id=video_id
            )
            response = request.execute()
            
            if response['items']:
                video = response['items'][0]
                return {
                    'title': video['snippet']['title'],
                    'description': video['snippet']['description'][:200] + '...',
                    'channel': video['snippet']['channelTitle'],
                    'published_at': video['snippet']['publishedAt'],
                    'view_count': video['statistics'].get('viewCount', 0),
                    'like_count': video['statistics'].get('likeCount', 0),
                    'comment_count': video['statistics'].get('commentCount', 0),
                    'thumbnail': video['snippet']['thumbnails']['medium']['url']
                }
        except Exception as e:
            logger.error(f"Error fetching video info: {e}")
            return None

    def get_comments(self, video_id, max_results=500):
        """Fetch comments from a YouTube video."""
        comments = []
        request = self.youtube.commentThreads().list(
            part="snippet",
            videoId=video_id,
            maxResults=100
        )
        
        try:
            response = request.execute()
            
            while response and len(comments) < max_results:
                for item in response['items']:
                    comment_data = item['snippet']['topLevelComment']['snippet']
                    comments.append({
                        "Author": comment_data['authorDisplayName'],
                        "Comment": comment_data['textDisplay'],
                        "Published": comment_data['publishedAt'],
                        "Likes": comment_data.get('likeCount', 0)
                    })
                
                if 'nextPageToken' in response and len(comments) < max_results:
                    request = self.youtube.commentThreads().list(
                        part="snippet",
                        videoId=video_id,
                        pageToken=response['nextPageToken'],
                        maxResults=100
                    )
                    response = request.execute()
                else:
                    break
                    
        except Exception as e:
            logger.error(f"Error fetching comments: {e}")
            
        return comments

    def is_english(self, text):
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

    def detect_sarcasm(self, comment):
        """Detect sarcasm in YouTube comments using rule-based approach."""
        if not isinstance(comment, str):
            return 'not sarcastic'
            
        text = comment.lower()
        sentiment = self.sia.polarity_scores(text)
        compound = sentiment['compound']
        
        contains_neg_context = any(kw in text for kw in self.negative_context_keywords)
        contains_sarcasm_clue = any(kw in text for kw in self.sarcasm_indicators)
        contains_emoji = any(e in text for e in self.emoji_indicators)
        has_caps_exaggeration = bool(re.search(r'\b[A-Z]{2,}\b', comment))
        
        if (compound > 0.4) and (contains_neg_context or contains_sarcasm_clue or 
                                contains_emoji or has_caps_exaggeration):
            return 'sarcastic'
        
        if compound < 0.4 and (contains_sarcasm_clue or contains_emoji) and contains_neg_context:
            return 'sarcastic'
        
        return 'not sarcastic'

    def classify_sentiment(self, text):
        """Classify sentiment using VADER sentiment analyzer."""
        if not isinstance(text, str) or text.strip() == '':
            return 'neutral'

        score = self.sia.polarity_scores(text)
        compound = score['compound']

        if compound >= 0.05:
            return 'positive'
        elif compound <= -0.05:
            return 'negative'
        else:
            return 'neutral'

    def analyze_comments(self, comments):
        """Analyze comments and return comprehensive results."""
        if not comments:
            return None
            
        df = pd.DataFrame(comments)
        
        # Filter English comments
        df['is_english'] = df['Comment'].apply(self.is_english)
        df_english = df[df['is_english'] == True].copy()
        
        # If no English comments found, use all comments
        if len(df_english) == 0:
            df_english = df.copy()
        
        # Add sentiment analysis
        df_english['sentiment'] = df_english['Comment'].apply(self.classify_sentiment)
        df_english['sarcasm_label'] = df_english['Comment'].apply(self.detect_sarcasm)
        
        # Calculate statistics
        sentiment_stats = df_english['sentiment'].value_counts(normalize=True) * 100
        sarcasm_stats = df_english['sarcasm_label'].value_counts(normalize=True) * 100
        
        # Prepare sample comments
        sample_comments = df_english[['Author', 'Comment', 'sentiment', 'sarcasm_label']].head(10).to_dict('records')
        
        return {
            'total_comments': len(df),
            'english_comments': len(df_english),
            'sentiment_distribution': sentiment_stats.round(2).to_dict(),
            'sarcasm_distribution': sarcasm_stats.round(2).to_dict(),
            'sample_comments': sample_comments,
            'analysis_timestamp': datetime.now().isoformat()
        }

# Initialize analyzer with API key from environment
YOUTUBE_API_KEY = os.getenv("YOUTUBE_API_KEY", "AIzaSyDfnWlqiN27t-8OrlRZHAjjH8oL1UahToo")
analyzer = YouTubeCommentAnalyzer(YOUTUBE_API_KEY)

def generate_tagged_video_insights(analysis_summary, video_info=None, sample_comments=None):
    """
    Generate detailed, context-aware insights for YouTube content creators
    """
    sentiment = analysis_summary.get("sentiment_distribution", {})
    sarcasm = analysis_summary.get("sarcasm_distribution", {})
    total_comments = analysis_summary.get("total_comments", 0)
    
    # Extract additional context
    channel_name = video_info.get("channel", "Unknown") if video_info else "Unknown"
    view_count = int(video_info.get("view_count", 0)) if video_info else 0
    like_count = int(video_info.get("like_count", 0)) if video_info else 0
    comment_count = int(video_info.get("comment_count", 0)) if video_info else 0
    video_title = video_info.get("title", "") if video_info else ""
    
    # Calculate engagement metrics
    engagement_rate = (like_count / view_count * 100) if view_count > 0 else 0
    comment_rate = (comment_count / view_count * 100) if view_count > 0 else 0
    
    # Analyze comment patterns for deeper insights
    top_keywords = extract_comment_keywords(sample_comments) if sample_comments else []
    sentiment_intensity = calculate_sentiment_intensity(sentiment)
    
    prompt = f"""
    You are an expert YouTube analytics consultant analyzing "{video_title}" by {channel_name}.
    
    VIDEO PERFORMANCE DATA:
    - Views: {view_count:,}
    - Likes: {like_count:,}
    - Comments: {comment_count:,}
    - Engagement Rate: {engagement_rate:.2f}%
    - Comment Rate: {comment_rate:.3f}%
    
    SENTIMENT ANALYSIS:
    - Positive: {sentiment.get('positive', 0):.1f}%
    - Negative: {sentiment.get('negative', 0):.1f}%
    - Neutral: {sentiment.get('neutral', 0):.1f}%
    - Sarcasm Rate: {sarcasm.get('sarcastic', 0):.1f}%
    
    COMMENT INSIGHTS:
    - Total Comments Analyzed: {total_comments}
    - Top Discussion Topics: {', '.join(top_keywords[:5]) if top_keywords else 'General feedback'}
    - Sentiment Intensity: {sentiment_intensity}
    
    Generate exactly 5 specific, actionable insights comparing this creator's performance to industry benchmarks and similar content creators. Each insight should be data-driven and include specific recommendations:

    1. "High Impact" - Focus on major optimization opportunities that could significantly boost performance
    2. "Medium Impact" - Identify moderate improvements with good ROI potential  
    3. "Content" - Analyze content quality, topics, and audience reception compared to successful creators
    4. "Sponsorship" - Evaluate brand partnership potential and audience commercial receptivity
    5. "Comment Sentiment" - Deep dive into audience engagement quality and community health

    For each insight, provide:
    - Specific metric comparisons (e.g., "15% above average", "Below top 25% threshold")
    - Actionable recommendations with expected impact
    - Industry context and competitor benchmarks where relevant
    - Concrete next steps the creator can implement

    Respond in JSON format:
    [
        {{
            "tag": "High Impact",
            "description": "Engagement rate at {engagement_rate:.1f}% is 23% below top creators in your niche - optimize thumbnails and first 15 seconds to boost retention",
            "details": "Industry average for similar channels is 4.2%. Focus on A/B testing thumbnails and improving hook within first 15 seconds.",
            "recommendation": "Test 3 thumbnail variants weekly, analyze top competitor hooks, aim for 30% engagement increase",
            "benchmark": "Target: 4.5% engagement rate (top 25% threshold for your category)"
        }},
        ...
    ]
    """

    try:
        response = client.chat.completions.create(
            model="gpt-4",  # Using GPT-4 for better analysis
            messages=[
                {
                    "role": "system", 
                    "content": """You are a senior YouTube analytics expert who provides data-driven insights to content creators. 
                    You have deep knowledge of YouTube algorithm, creator economy trends, and cross-channel performance benchmarks. 
                    Your insights should be specific, actionable, and include concrete metrics and comparisons."""
                },
                {"role": "user", "content": prompt}
            ],
            temperature=0.3,  # Lower temperature for more consistent, analytical responses
            max_tokens=800
        )
        
        raw = response.choices[0].message.content.strip()
        print("OpenAI enhanced response:", raw)

        # Clean and parse JSON response
        cleaned = re.sub(r"^```(?:json)?\s*|```$", "", raw.strip(), flags=re.MULTILINE).strip()
        insights = json.loads(cleaned)
        
        # Validate and enhance insights structure
        enhanced_insights = []
        required_tags = ["High Impact", "Medium Impact", "Content", "Sponsorship", "Comment Sentiment"]
        
        for i, tag in enumerate(required_tags):
            if i < len(insights):
                insight = insights[i]
                enhanced_insights.append({
                    "tag": tag,
                    "description": insight.get("description", f"Analysis for {tag} category"),
                    "details": insight.get("details", "Detailed analysis not available"),
                    "recommendation": insight.get("recommendation", "Recommendations pending further analysis"),
                    "benchmark": insight.get("benchmark", "Benchmark data being compiled"),
                    "priority": get_priority_level(tag),
                    "estimated_impact": estimate_impact_score(sentiment, engagement_rate, tag)
                })
        
        return enhanced_insights

    except json.JSONDecodeError as e:
        print(f"JSON parsing error: {e}")
        return generate_fallback_insights(analysis_summary, video_info)
    except Exception as e:
        print(f"OpenAI error: {e}")
        return generate_fallback_insights(analysis_summary, video_info)


def extract_comment_keywords(sample_comments):
    """Extract key themes and topics from comments"""
    if not sample_comments:
        return []
    
    # Combine all comments
    all_text = " ".join([comment.get("Comment", "") for comment in sample_comments])
    
    # Simple keyword extraction (you could enhance this with NLP libraries)
    common_words = ["great", "good", "bad", "love", "hate", "amazing", "awesome", 
                   "terrible", "boring", "interesting", "funny", "hilarious"]
    
    found_keywords = []
    for word in common_words:
        if word.lower() in all_text.lower():
            found_keywords.append(word)
    
    return found_keywords[:10]  # Return top 10


def calculate_sentiment_intensity(sentiment_dist):
    """Calculate overall sentiment intensity"""
    positive = sentiment_dist.get('positive', 0)
    negative = sentiment_dist.get('negative', 0)
    
    if positive > 60:
        return "Highly Positive"
    elif positive > 40:
        return "Moderately Positive"
    elif negative > 40:
        return "Moderately Negative"
    elif negative > 25:
        return "Mixed with Negative Lean"
    else:
        return "Balanced"


def get_priority_level(tag):
    """Assign priority levels to different insight categories"""
    priority_map = {
        "High Impact": "Critical",
        "Medium Impact": "Important", 
        "Content": "Important",
        "Sponsorship": "Moderate",
        "Comment Sentiment": "Moderate"
    }
    return priority_map.get(tag, "Low")


def estimate_impact_score(sentiment_dist, engagement_rate, tag):
    """Estimate potential impact score for each insight category"""
    positive_rate = sentiment_dist.get('positive', 0)
    negative_rate = sentiment_dist.get('negative', 0)
    
    base_scores = {
        "High Impact": 85,
        "Medium Impact": 65,
        "Content": 70,
        "Sponsorship": 50,
        "Comment Sentiment": 60
    }
    
    base = base_scores.get(tag, 50)
    
    # Adjust based on current performance
    if engagement_rate > 5:
        base += 10
    elif engagement_rate < 2:
        base -= 15
    
    if positive_rate > 50:
        base += 5
    elif negative_rate > 30:
        base -= 10
    
    return min(95, max(20, base))  # Keep score between 20-95


def generate_fallback_insights(analysis_summary, video_info):
    """Generate basic insights when AI generation fails"""
    sentiment = analysis_summary.get("sentiment_distribution", {})
    
    return [
        {
            "tag": "High Impact",
            "description": f"Sentiment analysis shows {sentiment.get('positive', 0):.1f}% positive feedback",
            "details": "Basic sentiment analysis completed successfully",
            "recommendation": "Monitor comment patterns and optimize based on feedback",
            "benchmark": "Industry average varies by niche",
            "priority": "Critical",
            "estimated_impact": 70
        },
        {
            "tag": "Medium Impact", 
            "description": "Engagement metrics available for optimization",
            "details": "Standard engagement analysis completed",
            "recommendation": "Focus on consistent posting and audience interaction",
            "benchmark": "Benchmark data being compiled",
            "priority": "Important",
            "estimated_impact": 60
        },
        {
            "tag": "Content",
            "description": "Content resonance varies across audience segments", 
            "details": "Comment sentiment provides content feedback insights",
            "recommendation": "Analyze top-performing content themes",
            "benchmark": "Compare with similar creators in your niche",
            "priority": "Important", 
            "estimated_impact": 65
        },
        {
            "tag": "Sponsorship",
            "description": "Audience receptivity to brand partnerships measurable",
            "details": "Comment analysis can indicate commercial readiness",
            "recommendation": "Build trust before introducing sponsored content",
            "benchmark": "Sponsorship readiness varies by audience maturity",
            "priority": "Moderate",
            "estimated_impact": 45
        },
        {
            "tag": "Comment Sentiment",
            "description": f"Community sentiment: {sentiment.get('positive', 0):.1f}% positive, {sentiment.get('negative', 0):.1f}% negative",
            "details": "Sentiment distribution analysis completed successfully",
            "recommendation": "Engage with positive comments and address negative feedback constructively", 
            "benchmark": "Healthy channels maintain 60%+ positive sentiment",
            "priority": "Moderate",
            "estimated_impact": 55
        }
    ]

def calculate_overall_sentiment_score(analysis_results):
    """Calculate a weighted sentiment score (0-100)"""
    sentiment = analysis_results.get("sentiment_distribution", {})
    positive = sentiment.get('positive', 0)
    negative = sentiment.get('negative', 0)
    neutral = sentiment.get('neutral', 0)
    
    # Weight positive higher, penalize negative more
    score = (positive * 1.0 + neutral * 0.5 - negative * 0.7)
    return max(0, min(100, score))


def assess_community_health(analysis_results):
    """Assess overall community health based on sentiment and engagement"""
    sentiment = analysis_results.get("sentiment_distribution", {})
    sarcasm = analysis_results.get("sarcasm_distribution", {})
    
    positive_rate = sentiment.get('positive', 0)
    negative_rate = sentiment.get('negative', 0)
    sarcasm_rate = sarcasm.get('sarcastic', 0)
    
    if positive_rate > 60 and sarcasm_rate < 15:
        return {"status": "Excellent", "score": 85}
    elif positive_rate > 45 and negative_rate < 25:
        return {"status": "Good", "score": 70}
    elif positive_rate > 30 and negative_rate < 40:
        return {"status": "Fair", "score": 55}
    else:
        return {"status": "Needs Attention", "score": 35}


def assess_content_performance(video_info, analysis_results):
    """Assess content performance against typical benchmarks"""
    view_count = int(video_info.get('view_count', 0))
    like_count = int(video_info.get('like_count', 0))
    comment_count = int(video_info.get('comment_count', 0))
    
    # Calculate ratios
    like_ratio = (like_count / max(view_count, 1)) * 100
    comment_ratio = (comment_count / max(view_count, 1)) * 100
    
    # Simple performance assessment
    performance_score = 0
    
    if like_ratio > 3:
        performance_score += 30
    elif like_ratio > 1.5:
        performance_score += 20
    elif like_ratio > 0.5:
        performance_score += 10
    
    if comment_ratio > 0.5:
        performance_score += 25
    elif comment_ratio > 0.2:
        performance_score += 15
    elif comment_ratio > 0.1:
        performance_score += 10
    
    # Sentiment bonus
    positive_rate = analysis_results.get("sentiment_distribution", {}).get('positive', 0)
    if positive_rate > 50:
        performance_score += 20
    elif positive_rate > 35:
        performance_score += 10
    
    if performance_score > 70:
        status = "High Performing"
    elif performance_score > 50:
        status = "Above Average"
    elif performance_score > 30:
        status = "Average"
    else:
        status = "Below Average"
    
    return {
        "status": status,
        "score": min(100, performance_score),
        "like_ratio": round(like_ratio, 3),
        "comment_ratio": round(comment_ratio, 3)
    }


def generate_priority_recommendations(tagged_insights):
    """Generate prioritized action items based on insights"""
    recommendations = []
    
    for insight in tagged_insights:
        if insight.get('priority') == 'Critical':
            recommendations.append({
                "priority": "High",
                "category": insight['tag'],
                "action": insight['recommendation'],
                "impact": insight.get('estimated_impact', 50)
            })
    
    # Sort by estimated impact
    recommendations.sort(key=lambda x: x['impact'], reverse=True)
    return recommendations[:3]  # Return top 3


def generate_benchmark_comparison(video_info, analysis_results):
    """Generate benchmark comparisons with industry standards"""
    view_count = int(video_info.get('view_count', 0))
    like_count = int(video_info.get('like_count', 0))
    comment_count = int(video_info.get('comment_count', 0))
    
    engagement_rate = (like_count / max(view_count, 1)) * 100
    comment_rate = (comment_count / max(view_count, 1)) * 100
    positive_sentiment = analysis_results.get("sentiment_distribution", {}).get('positive', 0)
    
    # Industry benchmarks (these are example values - you'd want real data)
    benchmarks = {
        "engagement_rate": {
            "your_rate": round(engagement_rate, 2),
            "industry_average": 2.5,
            "top_25_percent": 4.2,
            "status": "above" if engagement_rate > 2.5 else "below"
        },
        "comment_rate": {
            "your_rate": round(comment_rate, 3),
            "industry_average": 0.3,
            "top_25_percent": 0.8,
            "status": "above" if comment_rate > 0.3 else "below"
        },
        "positive_sentiment": {
            "your_rate": round(positive_sentiment, 1),
            "industry_average": 45.0,
            "top_25_percent": 65.0,
            "status": "above" if positive_sentiment > 45 else "below"
        }
    }
    
    return benchmarks

# Enable CORS for all routes with comprehensive configuration
CORS(app, 
     resources={r"/api/*": {"origins": "*"}},
     methods=["GET", "POST", "PUT", "DELETE", "OPTIONS"],
     allow_headers=["Content-Type", "Authorization", "X-Requested-With"],
     supports_credentials=True)

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

YOUTUBE_API_KEY = os.environ.get("YOUTUBE_API_KEY", "YOUR_API_KEY")
analytics_agent = AnalyticsAgent(YOUTUBE_API_KEY)

@app.route('/health', methods=['GET'])
def health():
    """Health check endpoint"""
    return jsonify({
        "status": "healthy",
        "service": "YouTube Analytics AI Backend",
        "version": "1.0.0"
    })


@app.route('/api/analyze', methods=['POST'])
def analyze_video():
    """Analyze YouTube video comments with enhanced insights."""
    try:
        data = request.get_json()
        video_url = data.get('video_url')
        
        if not video_url:
            return jsonify({"error": "video_url is required"}), 400
        
        # Extract video ID
        video_id = analyzer.extract_video_id(video_url)
        if not video_id:
            return jsonify({"error": "Invalid YouTube URL"}), 400
        
        # Get video info
        video_info = analyzer.get_video_info(video_id)
        if not video_info:
            return jsonify({"error": "Video not found or private"}), 404
        
        # Get comments
        comments = analyzer.get_comments(video_id)
        if not comments:
            return jsonify({"error": "No comments found or comments are disabled"}), 404
        
        # Analyze comments
        analysis_results = analyzer.analyze_comments(comments)
        if not analysis_results:
            return jsonify({"error": "Failed to analyze comments"}), 500

        # Generate enhanced tagged insights with video context
        tagged_insights = generate_tagged_video_insights(
            analysis_results, 
            video_info, 
            analysis_results.get('sample_comments', [])
        )
        
        print("Generated enhanced insights:", tagged_insights)
        
        # Calculate additional metrics for frontend
        additional_metrics = {
            "engagement_rate": (int(video_info.get('like_count', 0)) / 
                              max(int(video_info.get('view_count', 1)), 1) * 100),
            "comment_rate": (int(video_info.get('comment_count', 0)) / 
                           max(int(video_info.get('view_count', 1)), 1) * 100),
            "sentiment_score": calculate_overall_sentiment_score(analysis_results),
            "community_health": assess_community_health(analysis_results),
            "content_performance": assess_content_performance(video_info, analysis_results)
        }
        
        return jsonify({
            "success": True,
            "video_info": video_info,
            "analysis": analysis_results,
            "tagged_insights": tagged_insights,
            "additional_metrics": additional_metrics,
            "recommendations": generate_priority_recommendations(tagged_insights),
            "benchmark_comparison": generate_benchmark_comparison(video_info, analysis_results)
        })
        
    except Exception as e:
        logger.error(f"Error in analyze_video: {e}")
        return jsonify({"error": f"Internal server error: {str(e)}"}), 500


@app.route('/api/<path:path>', methods=['OPTIONS'])
def handle_preflight(path):
    """Handle preflight OPTIONS requests for CORS"""
    response = jsonify({'status': 'ok'})
    response.headers.add('Access-Control-Allow-Origin', '*')
    response.headers.add('Access-Control-Allow-Headers', 'Content-Type,Authorization,X-Requested-With')
    response.headers.add('Access-Control-Allow-Methods', 'GET,POST,PUT,DELETE,OPTIONS')
    response.headers.add('Access-Control-Allow-Credentials', 'true')
    return response

@app.route('/api/analytics', methods=['POST'])
def get_analytics():
    """Get video analytics only (basic stats)"""
    try:
        data = request.get_json()
        video_id = data.get('video_id')
        if not video_id:
            return jsonify({"error": "Video ID is required"}), 400
        result = analytics_agent.process({'video_id': video_id})
        return jsonify(result)
    except Exception as e:
        logger.error(f"Error in get_analytics: {str(e)}")
        return jsonify({"error": str(e)}), 500

@app.route('/api/comments', methods=['POST'])
def get_comments():
    """Get video comments analysis (sentiment and basic stats)"""
    try:
        data = request.get_json()
        video_id = data.get('video_id')
        if not video_id:
            return jsonify({"error": "Video ID is required"}), 400
        result = analytics_agent.comment_analytics(video_id)
        return jsonify(result)
    except Exception as e:
        logger.error(f"Error in get_comments: {str(e)}")
        return jsonify({"error": str(e)}), 500

@app.route('/api/channel/<channel_id>', methods=['GET'])
def channel_analytics(channel_id):
    """Get comprehensive channel analytics"""
    try:
        result = analytics_agent.get_channel_analytics(channel_id)
        return jsonify(result)
    except Exception as e:
        logger.error(f"Error in channel_analytics: {str(e)}")
        return jsonify({"error": str(e)}), 500

@app.route('/api/channel/compare', methods=['POST'])
def compare_channels():
    """Compare multiple channels"""
    try:
        data = request.get_json()
        channel_ids = data.get('channel_ids', [])
        
        if not channel_ids:
            return jsonify({"error": "No channel IDs provided"}), 400
        
        result = analytics_agent.compare_channels(channel_ids)
        return jsonify(result)
    except Exception as e:
        logger.error(f"Error in compare_channels: {str(e)}")
        return jsonify({"error": str(e)}), 500

@app.route('/api/metrics', methods=['GET'])
def get_metrics():
    """Get aggregated analytics metrics"""
    try:
        result = analytics_agent.get_metrics()
        return jsonify(result)
    except Exception as e:
        logger.error(f"Error in get_metrics: {str(e)}")
        return jsonify({"error": str(e)}), 500

@app.route('/api/history', methods=['GET'])
def get_history():
    """Get analysis history"""
    try:
        result = analytics_agent.get_history()
        return jsonify(result)
    except Exception as e:
        logger.error(f"Error in get_history: {str(e)}")
        return jsonify({"error": str(e)}), 500

@app.route('/api/video/compare', methods=['POST'])
def compare_videos_by_keywords():
    """Compare a video with similar videos based on keywords/tags."""
    try:
        data = request.get_json()
        video_id = data.get('video_id')
        max_results = data.get('max_results', 5)
        
        if not video_id:
            return jsonify({"error": "video_id is required"}), 400
        
        result = analytics_agent.compare_videos_by_keywords(video_id, max_results)
        return jsonify(result)
        
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route('/api/video/technical-insights', methods=['POST'])
def get_technical_insights():
    """Get detailed technical insights and success patterns for a video."""
    try:
        data = request.get_json()
        video_id = data.get('video_id')
        max_results = data.get('max_results', 5)
        
        if not video_id:
            return jsonify({"error": "video_id is required"}), 400
        
        result = analytics_agent.get_technical_insights(video_id, max_results)
        return jsonify(result)
        
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route('/api/video/sponsorship/<video_id>', methods=['GET'])
def analyze_video_sponsorship(video_id):
    """Analyze sponsorships in a specific video"""
    try:
        result = analytics_agent.analyze_sponsorships(video_id)
        return jsonify(result)
    except Exception as e:
        logger.error(f"Error in analyze_video_sponsorship: {str(e)}")
        return jsonify({"error": str(e)}), 500

@app.route('/api/video/search-sponsored', methods=['POST'])
def search_sponsored_videos():
    """Search for videos with keywords and analyze their sponsorship patterns"""
    try:
        data = request.get_json()
        keywords = data.get('keywords')
        max_results = data.get('max_results', 10)
        
        if not keywords:
            return jsonify({"error": "Keywords are required"}), 400
        
        result = analytics_agent.search_sponsored_videos(keywords, max_results)
        return jsonify(result)
    except Exception as e:
        logger.error(f"Error in search_sponsored_videos: {str(e)}")
        return jsonify({"error": str(e)}), 500

@app.route('/api/video/enhanced-insights', methods=['POST'])
def get_enhanced_insights():
    """Get enhanced insights including performance prediction, audience behavior, and optimization suggestions"""
    try:
        data = request.get_json()
        video_id = data.get('video_id')
        
        if not video_id:
            return jsonify({"error": "video_id is required"}), 400
        
        result = analytics_agent.get_enhanced_insights(video_id)
        return jsonify(result)
        
    except Exception as e:
        logger.error(f"Error in get_enhanced_insights: {str(e)}")
        return jsonify({"error": str(e)}), 500

@app.route('/api/content-gap-analysis', methods=['POST'])
def get_content_gap_analysis():
    """Analyze content gaps in the niche"""
    try:
        data = request.get_json()
        channel_id = data.get('channel_id')
        niche_keywords = data.get('niche_keywords', [])
        
        if not channel_id:
            return jsonify({"error": "channel_id is required"}), 400
        
        if not niche_keywords:
            return jsonify({"error": "niche_keywords are required"}), 400
        
        result = analytics_agent.get_content_gap_analysis(channel_id, niche_keywords)
        return jsonify(result)
        
    except Exception as e:
        logger.error(f"Error in get_content_gap_analysis: {str(e)}")
        return jsonify({"error": str(e)}), 500

@app.route('/api/trend-analysis', methods=['POST'])
def get_trend_analysis():
    """Analyze trends for given keywords"""
    try:
        data = request.get_json()
        keywords = data.get('keywords', [])
        
        if not keywords:
            return jsonify({"error": "keywords are required"}), 400
        
        result = analytics_agent.get_trend_analysis(keywords)
        return jsonify(result)
        
    except Exception as e:
        logger.error(f"Error in get_trend_analysis: {str(e)}")
        return jsonify({"error": str(e)}), 500

@app.route('/api/competitor-analysis', methods=['POST'])
def get_competitor_analysis():
    """Analyze performance against competitors"""
    try:
        data = request.get_json()
        channel_id = data.get('channel_id')
        competitor_channels = data.get('competitor_channels', [])
        
        if not channel_id:
            return jsonify({"error": "channel_id is required"}), 400
        
        if not competitor_channels:
            return jsonify({"error": "competitor_channels are required"}), 400
        
        result = analytics_agent.get_competitor_analysis(channel_id, competitor_channels)
        return jsonify(result)
        
    except Exception as e:
        logger.error(f"Error in get_competitor_analysis: {str(e)}")
        return jsonify({"error": str(e)}), 500

@app.route('/api/insights/summary', methods=['POST'])
def get_insights_summary():
    """Get a comprehensive insights summary for a video"""
    try:
        data = request.get_json()
        video_id = data.get('video_id')
        
        if not video_id:
            return jsonify({"error": "video_id is required"}), 400
        
        # Get all types of insights
        basic_analytics = analytics_agent.process({'video_id': video_id})
        comment_analytics = analytics_agent.comment_analytics(video_id)
        enhanced_insights = analytics_agent.get_enhanced_insights(video_id)
        technical_insights = analytics_agent.get_technical_insights(video_id, 3)
        
        # Combine all insights
        comprehensive_summary = {
            "video_id": video_id,
            "basic_analytics": basic_analytics,
            "comment_analytics": comment_analytics,
            "enhanced_insights": enhanced_insights,
            "technical_insights": technical_insights,
            "summary": {
                "total_insights": len(enhanced_insights.get("ai_insights", {}).get("key_insights", [])),
                "performance_score": enhanced_insights.get("performance_prediction", {}).get("performance_score", 0),
                "recommendations_count": len(enhanced_insights.get("optimization_suggestions", {}).get("suggestions", {}).get("title_optimization", [])),
                "risk_factors": len(enhanced_insights.get("performance_prediction", {}).get("risk_factors", []))
            }
        }
        
        return jsonify(comprehensive_summary)
        
    except Exception as e:
        logger.error(f"Error in get_insights_summary: {str(e)}")
        return jsonify({"error": str(e)}), 500

@app.route('/api/extract-video-id', methods=['POST'])
def extract_video_id():
    """Extract video ID from URL."""
    try:
        data = request.get_json()
        video_url = data.get('video_url')
        
        if not video_url:
            return jsonify({"error": "video_url is required"}), 400
        
        video_id = analyzer.extract_video_id(video_url)
        if not video_id:
            return jsonify({"error": "Invalid YouTube URL"}), 400
        
        return jsonify({"video_id": video_id})
        
    except Exception as e:
        logger.error(f"Error in extract_video_id: {e}")
        return jsonify({"error": f"Internal server error: {str(e)}"}), 500

@app.route("/routes", methods=["GET"])
def list_routes():
    """List all available API routes"""
    import urllib
    output = []
    for rule in app.url_map.iter_rules():
        methods = ','.join(rule.methods)
        line = urllib.parse.unquote(f"{rule.endpoint:30s} {methods:20s} {rule}")
        output.append(line)
    return "<br>".join(sorted(output))

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8000, debug=True)

@app.after_request
def after_request(response):
    """Add CORS headers to all responses"""
    response.headers.add('Access-Control-Allow-Origin', '*')
    response.headers.add('Access-Control-Allow-Headers', 'Content-Type,Authorization,X-Requested-With')
    response.headers.add('Access-Control-Allow-Methods', 'GET,POST,PUT,DELETE,OPTIONS')
    response.headers.add('Access-Control-Allow-Credentials', 'true')
    return response 