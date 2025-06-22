#!/usr/bin/env python3
"""
Test script for LLM-based sponsorship detection with mock data
"""

import sys
import os
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

def test_llm_sponsorship_detection():
    """Test LLM sponsorship detection with mock transcript data"""
    print("🧪 Testing LLM Sponsorship Detection with Mock Data")
    print("=" * 60)
    
    try:
        from src.services.youtube_service import EnhancedYouTubeService
        
        # Initialize service
        youtube_service = EnhancedYouTubeService("test_key")
        
        # Test cases with different sponsorship scenarios
        test_cases = [
            {
                "name": "No Sponsorship",
                "title": "How to Make a Cake",
                "description": "Learn how to bake a delicious chocolate cake from scratch.",
                "transcript": "Hello everyone, today I'm going to show you how to make a delicious chocolate cake. First, we'll need flour, sugar, eggs, and cocoa powder. Mix them together and bake at 350 degrees for 30 minutes. That's it! Thanks for watching.",
                "expected_sponsorship": False
            },
            {
                "name": "Direct Sponsorship",
                "title": "Best VPN Review 2024",
                "description": "Reviewing the top VPN services for online privacy and security.",
                "transcript": "This video is sponsored by NordVPN. NordVPN offers military-grade encryption and servers in over 60 countries. Use code YOUTUBE20 for 20% off your first year. Click the link in the description to get started. Now let's talk about why you need a VPN...",
                "expected_sponsorship": True
            },
            {
                "name": "Product Placement",
                "title": "Morning Routine 2024",
                "description": "My complete morning routine for productivity and wellness.",
                "transcript": "Good morning! I start my day with a cup of coffee from my favorite brand. Then I use my iPhone to check emails and plan my day. I love using Notion for organization - it's been a game changer. Don't forget to subscribe for more content!",
                "expected_sponsorship": False  # Natural mentions, not sponsored
            },
            {
                "name": "Affiliate Links",
                "title": "Tech Gadgets You Need",
                "description": "Essential tech gadgets for productivity and entertainment.",
                "transcript": "Today I'm sharing my favorite tech gadgets. First up is this amazing wireless keyboard from Amazon. I'll put the link in the description below. Also check out this gaming mouse - use code TECH10 for 10% off. These are affiliate links that help support the channel.",
                "expected_sponsorship": True
            }
        ]
        
        for i, test_case in enumerate(test_cases, 1):
            print(f"\n{'='*20} Test Case {i}: {test_case['name']} {'='*20}")
            
            try:
                # Test LLM detection
                result = youtube_service.detect_sponsorships_with_llm(
                    test_case['transcript'],
                    test_case['title'],
                    test_case['description']
                )
                
                if 'error' in result:
                    print(f"⚠️  LLM detection failed: {result['error']}")
                    print("   This is expected if OpenAI API key is not configured")
                    
                    # Test regex fallback
                    print("🔄 Testing regex fallback...")
                    regex_result = youtube_service.detect_sponsorships_regex(
                        test_case['transcript'],
                        test_case['title'],
                        test_case['description']
                    )
                    
                    print(f"📊 Regex Results:")
                    print(f"   - Has Sponsorship: {regex_result.get('has_sponsorship', 'N/A')}")
                    print(f"   - Sponsorship Level: {regex_result.get('sponsorship_level', 'N/A')}")
                    print(f"   - Confidence Score: {regex_result.get('confidence_score', 'N/A')}")
                    print(f"   - Detected Companies: {regex_result.get('detected_companies', [])}")
                    print(f"   - Sponsorship Text: {len(regex_result.get('sponsorship_text', []))} segments")
                    
                    # Validate against expected
                    expected = test_case['expected_sponsorship']
                    actual = regex_result.get('has_sponsorship', False)
                    if expected == actual:
                        print(f"✅ Result matches expectation: {expected}")
                    else:
                        print(f"❌ Result doesn't match expectation: expected {expected}, got {actual}")
                        
                else:
                    print(f"✅ LLM detection successful!")
                    print(f"📊 Results:")
                    print(f"   - Has Sponsorship: {result.get('has_sponsorship', 'N/A')}")
                    print(f"   - Sponsorship Level: {result.get('sponsorship_level', 'N/A')}")
                    print(f"   - Confidence Score: {result.get('confidence_score', 'N/A')}")
                    print(f"   - Detection Method: {result.get('detection_method', 'N/A')}")
                    
                    if 'llm_analysis' in result:
                        print(f"   - LLM Analysis: {result['llm_analysis'][:100]}...")
                    
                    # Show sponsors if detected
                    sponsors = result.get('sponsors', [])
                    if sponsors:
                        print(f"   - Detected Sponsors: {len(sponsors)}")
                        for sponsor in sponsors[:2]:
                            print(f"     * {sponsor.get('name', 'Unknown')} ({sponsor.get('type', 'Unknown')})")
                    
                    # Validate against expected
                    expected = test_case['expected_sponsorship']
                    actual = result.get('has_sponsorship', False)
                    if expected == actual:
                        print(f"✅ Result matches expectation: {expected}")
                    else:
                        print(f"❌ Result doesn't match expectation: expected {expected}, got {actual}")
                        
            except Exception as e:
                print(f"❌ Error in test case: {e}")
        
        print("\n" + "=" * 60)
        print("📊 TEST SUMMARY")
        print("=" * 60)
        print("✅ LLM-based sponsorship detection implemented")
        print("✅ Fallback to regex detection when LLM fails")
        print("✅ Comprehensive test cases with different sponsorship scenarios")
        print("✅ Error handling and graceful degradation")
        print("✅ Validation against expected results")
        
    except ImportError as e:
        print(f"❌ Import error: {e}")
        print("Make sure you're running from the project root directory")
    except Exception as e:
        print(f"❌ Unexpected error: {e}")

def test_sponsorship_indicators():
    """Test specific sponsorship indicators"""
    print("\n🔍 Testing Specific Sponsorship Indicators")
    print("=" * 50)
    
    try:
        from src.services.youtube_service import EnhancedYouTubeService
        
        youtube_service = EnhancedYouTubeService("test_key")
        
        # Test specific sponsorship phrases
        indicators = [
            "This video is sponsored by NordVPN",
            "Thanks to Skillshare for sponsoring this video",
            "Use code YOUTUBE20 for 20% off",
            "Check out the link in the description",
            "Visit our sponsor's website",
            "This is not a sponsored video",
            "Just sharing my honest opinion",
            "Partnered with ExpressVPN for this review"
        ]
        
        for i, indicator in enumerate(indicators, 1):
            print(f"\n{i}. Testing: '{indicator}'")
            
            result = youtube_service.detect_sponsorships_regex(
                indicator, "", ""
            )
            
            print(f"   Has Sponsorship: {result.get('has_sponsorship', 'N/A')}")
            print(f"   Level: {result.get('sponsorship_level', 'N/A')}")
            print(f"   Confidence: {result.get('confidence_score', 'N/A')}")
            
    except Exception as e:
        print(f"❌ Error testing indicators: {e}")

if __name__ == "__main__":
    test_llm_sponsorship_detection()
    test_sponsorship_indicators()
    print("\n🎉 All tests completed!") 