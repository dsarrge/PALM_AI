# -*- coding: utf-8 -*-
"""
Created on Sun Feb  8 12:17:42 2026

@author: dsarr

PALM Food AI
"Today so far" CLI
food_ai/core/today_cli.py
"""

from datetime import date
from core.daily_totals import daily_totals

# Display helper
def print_today_summary():
    totals = daily_totals(date.today())

    print("\nPALM — TODAY SO FAR")
    print("=" * 32)

    if totals["items_logged"] == 0:
        print("No food logged yet today.")
        print("=" * 32)
        return

    print(f"Date:     {totals['date']}")
    print(f"Items:   {totals['items_logged']}")
    print("-" * 32)

    print(f"Calories: {round(totals['calories'])}")
    print(f"Protein:  {round(totals['protein_g'], 1)} g")
    print(f"Fat:      {round(totals['fat_g'], 1)} g")
    print(f"Carbs:    {round(totals['carbs_g'], 1)} g")
    print(f"Fiber:    {round(totals['fiber_g'], 1)} g")
    print(f"Sugar:    {round(totals['sugar_g'], 1)} g")
    print(f"Sodium:   {round(totals['sodium_mg'])} mg")

    print("=" * 32)

# CLI entry point
if __name__ == "__main__":
    print_today_summary()
