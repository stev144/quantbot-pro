"""
utils.py

Helper functions for formatting and calculations.
"""

def format_trade(trade: dict) -> str:
    """
    Convert trade dictionary into readable text.

    Args:
        trade (dict): Trade info

    Returns:
        str: formatted string
    """

    return f"{trade['type']} at {trade['price']}"