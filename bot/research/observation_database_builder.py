# ═══════════════════════════════════════════════════════════════════════════════════════════════════════════
# bot/research/observation_database_builder.py
#
# PHASE 1B: OBSERVATION DATABASE BUILDER
#
# Purpose:
#   Collect observations from historical data for statistical testing.
#   This is the FOUNDATION for all edge discovery.
#
# Process:
#   1. Load historical OHLCV data (multiple coins, multiple years)
#   2. Calculate ALL features for every candle (using FeatureCalculator)
#   3. Clean and validate data
#   4. Save to CSV for statistical analysis
#
# Output:
#   observations.csv with 50,000+ rows
#   One row per candle, all features + forward return label
#
# ═══════════════════════════════════════════════════════════════════════════════════════════════════════════

import logging
import pandas as pd
import numpy as np
from typing import Dict, List, Optional, Tuple
from pathlib import Path
from datetime import datetime
import json
from dataclasses import dataclass, asdict
import pickle

from bot.research.feature_calculator import FeatureCalculator

# ───────────────────────────────────────────────────────────────────────────────────────────────────────
# LOGGING
# ───────────────────────────────────────────────────────────────────────────────────────────────────────

logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(name)s | %(levelname)s | %(message)s")


# ═══════════════════════════════════════════════════════════════════════════════════════════════════════════
# OBSERVATION DATA STRUCTURE
# ═══════════════════════════════════════════════════════════════════════════════════════════════════════════

@dataclass
class ObservationMetadata:
    """Metadata about the observation database"""
    
    creation_date: str                    # When was this created?
    coins: List[str]                      # Which coins included?
    timeframe: str                        # 1h, 4h, 1d?
    date_range: Dict[str, str]            # {'from': '2020-01-01', 'to': '2025-01-15'}
    total_observations: int               # How many rows?
    features_included: List[str]          # All feature names
    null_count: Dict[str, int]            # How many nulls per feature?
    
    def save(self, path: Path):
        """Save metadata as JSON"""
        with open(path, 'w') as f:
            json.dump(asdict(self), f, indent=2)
        logger.info(f"✓ Metadata saved to {path}")
    
    @staticmethod
    def load(path: Path) -> 'ObservationMetadata':
        """Load metadata from JSON"""
        with open(path, 'r') as f:
            data = json.load(f)
        return ObservationMetadata(**data)


# ═══════════════════════════════════════════════════════════════════════════════════════════════════════════
# OBSERVATION DATABASE BUILDER
# ═══════════════════════════════════════════════════════════════════════════════════════════════════════════

class ObservationDatabaseBuilder:
    """
    Builds observation database from historical OHLCV data.
    
    Design:
    - Process multiple coins simultaneously
    - Handle missing/corrupt data gracefully
    - Track data quality metrics
    - Save in multiple formats (CSV for Excel, pickle for fast loading)
    - Create metadata for reproducibility
    """
    
    def __init__(self, output_dir: str = "research_data"):
        """
        Initialize builder.
        
        Args:
            output_dir: Where to save observations.csv and metadata
        """
        self.output_dir = Path(output_dir)
        self.output_dir.mkdir(parents=True, exist_ok=True)
        
        self.calculator = FeatureCalculator(min_data_required=100)
        self.observations = []
        self.metadata = None
        
        logger.info(f"ObservationDatabaseBuilder initialized (output: {self.output_dir})")
    
    # ───────────────────────────────────────────────────────────────────────────────────────────────────
    # MAIN METHOD: Build database from multiple coins
    # ───────────────────────────────────────────────────────────────────────────────────────────────────
    
    def build_from_multiple_coins(self, data_dict: Dict[str, pd.DataFrame], 
                                 timeframe: str = "1h") -> pd.DataFrame:
        """
        Build observation database from multiple coins.
        
        Args:
            data_dict (Dict): {symbol: DataFrame} e.g., {'BTCUSDT': df, 'ETHUSDT': df, ...}
            timeframe (str): Candle timeframe (for metadata)
        
        Returns:
            pd.DataFrame: Complete observation database
        """
        
        logger.info(f"Building observation database from {len(data_dict)} coins...")
        
        all_observations = []
        metadata_dict = {}
        
        # Process each coin
        for i, (symbol, df) in enumerate(data_dict.items(), 1):
            logger.info(f"[{i}/{len(data_dict)}] Processing {symbol} ({len(df)} candles)...")
            
            try:
                # Calculate features
                featured_df = self.calculator.calculate_all_features(df, symbol=symbol, timeframe=timeframe)
                
                # Remove rows with NaN in critical columns (first 100 or so don't have enough history)
                featured_df = self._clean_dataframe(featured_df)
                
                logger.info(f"  ✓ {symbol}: {len(featured_df)} valid observations")
                
                all_observations.append(featured_df)
                
                # Track metadata
                metadata_dict[symbol] = {
                    'raw_candles': len(df),
                    'valid_observations': len(featured_df),
                    'date_range': (str(df.index[0]), str(df.index[-1]))
                }
            
            except Exception as e:
                logger.error(f"  ✗ Error processing {symbol}: {e}")
                continue
        
        # Combine all coins into single dataframe
        logger.info("Combining all coins into single database...")
        combined_df = pd.concat(all_observations, ignore_index=False)
        combined_df = combined_df.sort_index()
        
        logger.info(f"✓ Combined database: {len(combined_df)} total observations")
        
        # Create and store metadata
        self._create_metadata(combined_df, timeframe, metadata_dict)
        
        return combined_df
    
    # ───────────────────────────────────────────────────────────────────────────────────────────────────
    # DATA CLEANING
    # ───────────────────────────────────────────────────────────────────────────────────────────────────
    
    def _clean_dataframe(self, df: pd.DataFrame, 
                        min_valid_features: int = 10) -> pd.DataFrame:
        """
        Clean dataframe by removing rows with too many NaNs.
        
        Early rows (first 50-100) will have NaNs because features need lookback window.
        That's OK - we just remove those rows.
        
        Args:
            df (pd.DataFrame): Featured dataframe
            min_valid_features: Minimum non-null values per row
        
        Returns:
            pd.DataFrame: Cleaned dataframe
        """
        
        try:
            # Count non-null values per row
            non_null_counts = df.notna().sum(axis=1)
            
            # Keep rows with enough valid features
            # (All feature columns + labels + timestamp/symbol)
            feature_columns = [col for col in df.columns 
                             if col not in ['symbol', 'timeframe']]
            min_required = len(feature_columns) - min_valid_features
            
            cleaned_df = df[non_null_counts >= min_required].copy()
            
            removed_count = len(df) - len(cleaned_df)
            logger.debug(f"Removed {removed_count} rows with insufficient data")
            
            return cleaned_df
        
        except Exception as e:
            logger.error(f"Error cleaning dataframe: {e}")
            return df
    
    # ───────────────────────────────────────────────────────────────────────────────────────────────────
    # METADATA CREATION
    # ───────────────────────────────────────────────────────────────────────────────────────────────────
    
    def _create_metadata(self, df: pd.DataFrame, timeframe: str, 
                        coin_metadata: Dict) -> None:
        """
        Create metadata about the observation database.
        
        Args:
            df (pd.DataFrame): Complete observation database
            timeframe (str): Candle timeframe
            coin_metadata (Dict): Metadata per coin
        """
        
        # Get date range
        if hasattr(df.index, 'to_pydatetime'):
            min_date = str(df.index.min())
            max_date = str(df.index.max())
        else:
            min_date = "unknown"
            max_date = "unknown"
        
        # Get coins
        coins = sorted(list(set(df.get('symbol', []))))
        
        # Count nulls per feature
        null_counts = df.isnull().sum().to_dict()
        
        # Create metadata
        self.metadata = ObservationMetadata(
            creation_date=datetime.now().isoformat(),
            coins=coins,
            timeframe=timeframe,
            date_range={'from': min_date, 'to': max_date},
            total_observations=len(df),
            features_included=list(df.columns),
            null_count=null_counts
        )
        
        logger.info(f"✓ Metadata created:")
        logger.info(f"  Coins: {coins}")
        logger.info(f"  Date range: {min_date} to {max_date}")
        logger.info(f"  Total observations: {len(df)}")
        logger.info(f"  Features: {len(df.columns)}")
    
    # ───────────────────────────────────────────────────────────────────────────────────────────────────
    # SAVE TO DISK
    # ───────────────────────────────────────────────────────────────────────────────────────────────────
    
    def save_database(self, df: pd.DataFrame, 
                     formats: List[str] = ['csv', 'pickle']) -> Dict[str, Path]:
        """
        Save observation database in multiple formats.
        
        Args:
            df (pd.DataFrame): Observation database
            formats (List[str]): Which formats to save ('csv', 'pickle', etc.)
        
        Returns:
            Dict[str, Path]: Saved file paths
        """
        
        saved_files = {}
        
        try:
            # CSV (human-readable, can open in Excel)
            if 'csv' in formats:
                csv_path = self.output_dir / "observations.csv"
                logger.info(f"Saving to CSV ({len(df)} rows × {len(df.columns)} cols)...")
                df.to_csv(csv_path)
                saved_files['csv'] = csv_path
                logger.info(f"✓ CSV saved to {csv_path}")
            
            # Pickle (fast loading for Python)
            if 'pickle' in formats:
                pickle_path = self.output_dir / "observations.pkl"
                logger.info("Saving to pickle...")
                df.to_pickle(pickle_path)
                saved_files['pickle'] = pickle_path
                logger.info(f"✓ Pickle saved to {pickle_path}")
            
            # Parquet (compressed, efficient)
            if 'parquet' in formats:
                parquet_path = self.output_dir / "observations.parquet"
                logger.info("Saving to parquet...")
                df.to_parquet(parquet_path)
                saved_files['parquet'] = parquet_path
                logger.info(f"✓ Parquet saved to {parquet_path}")
            
            # Save metadata
            if self.metadata:
                metadata_path = self.output_dir / "observations_metadata.json"
                self.metadata.save(metadata_path)
                saved_files['metadata'] = metadata_path
            
            logger.info(f"✓ All files saved to {self.output_dir}")
            return saved_files
        
        except Exception as e:
            logger.error(f"Error saving database: {e}", exc_info=True)
            return {}
    
    # ───────────────────────────────────────────────────────────────────────────────────────────────────
    # VALIDATION & STATISTICS
    # ───────────────────────────────────────────────────────────────────────────────────────────────────
    
    def validate_database(self, df: pd.DataFrame) -> Dict:
        """
        Validate observation database quality.
        
        Checks:
        - No duplicate rows
        - Sufficient data variety
        - Forward returns calculated
        - Features in reasonable ranges
        
        Args:
            df (pd.DataFrame): Observation database
        
        Returns:
            Dict: Validation report
        """
        
        report = {
            'total_rows': len(df),
            'duplicates': len(df[df.duplicated()]),
            'columns': len(df.columns),
            'null_percentages': (df.isnull().sum() / len(df) * 100).to_dict(),
            'forward_return_stats': {
                '4h_mean': df['forward_return_4h'].mean() if 'forward_return_4h' in df else None,
                '4h_std': df['forward_return_4h'].std() if 'forward_return_4h' in df else None,
                '4h_min': df['forward_return_4h'].min() if 'forward_return_4h' in df else None,
                '4h_max': df['forward_return_4h'].max() if 'forward_return_4h' in df else None,
            },
            'win_rate_4h': (df['win_4h'].sum() / len(df) * 100) if 'win_4h' in df else None,
        }
        
        # Validation checks
        checks = {
            'no_duplicates': report['duplicates'] == 0,
            'has_forward_returns': 'forward_return_4h' in df,
            'has_labels': 'win_4h' in df,
            'sufficient_rows': len(df) > 10000,
            'reasonable_null_percent': all(v < 20 for v in report['null_percentages'].values()),
        }
        
        report['validation_checks'] = checks
        report['is_valid'] = all(checks.values())
        
        return report
    
    def print_validation_report(self, report: Dict) -> None:
        """Print validation report in human-readable format"""
        
        logger.info("\n" + "="*80)
        logger.info("OBSERVATION DATABASE VALIDATION REPORT")
        logger.info("="*80)
        
        logger.info(f"\nDatabase Size:")
        logger.info(f"  Total rows: {report['total_rows']:,}")
        logger.info(f"  Total columns: {report['columns']}")
        logger.info(f"  Duplicates: {report['duplicates']}")
        
        logger.info(f"\nForward Returns (4h):")
        if report['forward_return_stats']['4h_mean'] is not None:
            logger.info(f"  Mean: {report['forward_return_stats']['4h_mean']:.4%}")
            logger.info(f"  Std Dev: {report['forward_return_stats']['4h_std']:.4%}")
            logger.info(f"  Min: {report['forward_return_stats']['4h_min']:.4%}")
            logger.info(f"  Max: {report['forward_return_stats']['4h_max']:.4%}")
            logger.info(f"  Win Rate: {report['win_rate_4h']:.1%}")
        
        logger.info(f"\nValidation Checks:")
        for check_name, result in report['validation_checks'].items():
            status = "✓" if result else "✗"
            logger.info(f"  {status} {check_name}")
        
        logger.info(f"\nOverall Status: {'✓ VALID' if report['is_valid'] else '✗ INVALID'}")
        logger.info("="*80 + "\n")


# ═══════════════════════════════════════════════════════════════════════════════════════════════════════════
# EXAMPLE USAGE
# ═══════════════════════════════════════════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    """
    Example: Build observation database from sample data
    """
    
    import warnings
    warnings.filterwarnings('ignore')
    
    # Create sample data for multiple coins
    coins_data = {}
    dates = pd.date_range('2022-01-01', periods=2000, freq='1h')
    np.random.seed(42)
    
    for symbol in ['BTCUSDT', 'ETHUSDT', 'BNBUSDT']:
        price = 30000 if 'BTC' in symbol else (2000 if 'ETH' in symbol else 300)
        
        coins_data[symbol] = pd.DataFrame({
            'open': price + np.random.randn(2000).cumsum() * 50,
            'high': price + np.random.randn(2000).cumsum() * 50 + 100,
            'low': price + np.random.randn(2000).cumsum() * 50 - 100,
            'close': price + np.random.randn(2000).cumsum() * 50,
            'volume': np.random.uniform(10000, 100000, 2000),
        }, index=dates)
    
    # Build database
    logger.info("Building observation database from sample data...")
    builder = ObservationDatabaseBuilder(output_dir="research_data")
    
    # Process multiple coins
    obs_df = builder.build_from_multiple_coins(coins_data, timeframe='1h')
    
    # Validate
    report = builder.validate_database(obs_df)
    builder.print_validation_report(report)
    
    # Save
    saved_files = builder.save_database(obs_df, formats=['csv', 'pickle'])
    
    logger.info(f"\n✓ Observation database built successfully!")
    logger.info(f"  Observations: {len(obs_df):,}")
    logger.info(f"  Features: {len(obs_df.columns)}")
    logger.info(f"  Saved to: {builder.output_dir}")