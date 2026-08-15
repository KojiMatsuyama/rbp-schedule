{-# LANGUAGE BangPatterns #-}
{-# LANGUAGE DerivingStrategies #-}
{-# LANGUAGE GeneralizedNewtypeDeriving #-}
{-# LANGUAGE LambdaCase #-}
{-# LANGUAGE RecordWildCards #-}
{-|
Module      : Data.RBP.Types
Description : Core algebraic types for the RBP (Reflect Block Pattern) engine.

This module defines the entire domain as algebraic data types.
Every \"if/else\" in the original procedural code becomes a
pattern-match on these types. The type system itself enforces
the RBP invariants:

  - Forward-only flow (direction :: ForwardOnly)
  - Strictly increasing bridge levels (enforced by construction)
  - Zero-vector = blocked (isZeroVector :: Vector -> Bool)
  - Uniform weights per bridge (validated at runtime)

The key insight: \"blocking\" is not a boolean flag — it is a
first-class algebraic value (Blocked At BridgeId Reason).
This eliminates the procedural distinction between \"false\" and
\"error\" that plagues imperative implementations.
-}
module Data.RBP.Types
  ( -- * Disease/Pest dimensions
    DiseaseIndex (..)
    -- * Entry Vector (Demand Layer)
  , EntryVector (EV)
  , mkEntryVector
  , isEntryActive
    -- * Evaluation Boxes (Bridge Layer)
  , EvalBoxId (..)
  , EvalBox (..)
  , mkEvalBox
    -- * Pesticides (SpecBridge Layer)
  , ToxicityClass (..)
  , RotationCategory (..)
  , Pesticide (..)
  , PesticideId (..)
  , TargetMatch (TM)
    -- * Bridge Weights (Reflect Layer primitives)
  , WeightAction (..)
  , BridgeWeight (..)
  , uniformWeight
  , bridgeWeightValue
    -- * Bridge (Reflect Layer gates)
  , BridgeId (..)
  , Bridge (..)
  , BridgeContext (..)
  , BridgeTrace (..)
    -- * Flow through bridges
  , FlowState (..)
  , FlowResult (..)
  , isBlocked
  , isFlowing
    -- * Prescription (Spec Layer)
  , PrescriptionSet (..)
  , ScoreBreakdown (..)
  , PrescriptionResult (..)
  , PrescriptionStatus (..)
    -- * Utility
  , Pretty (..)
  , vectorDim
  , dotProduct
  , cosineSimilarity
  , ForwardOnly (..)
  , hadamard
  , isZeroVector
  , dotProductInt
  , evToIntVector
  , evToDouble
  , countActive
  ) where

import qualified Data.Map.Strict as Map
import qualified Data.Vector.Unboxed as U
import Data.List (sortBy)
import Data.Ord (Down (..), comparing)

------------------------------------------------------------------------------
-- Disease / Pest dimensions
------------------------------------------------------------------------------

-- | The 10-dimensional meaning-space for disease/pest presence.
-- Each constructor is a \"semantic boundary\" — a direction in which
-- water (meaning) can flow.
data DiseaseIndex
  = Anthracnose          -- ^ idx 0: 炭疽病
  | GrayMold             -- ^ idx 1: 灰色かび病
  | PowderyMildew        -- ^ idx 2: うどんこ病
  | SpiderMite           -- ^ idx 3: ナミハダニ
  | Cutworm              -- ^ idx 4: ハスモンヨトウ
  | TobaccoBudworm       -- ^ idx 5: オオタバコガ
  | CitrusThrips         -- ^ idx 6: ミカンキイロアザミウマ
  | CottonStinkbug       -- ^ idx 7: ワタアブラムシ
  | Aphid                -- ^ idx 8: アブラムシ
  | Whitefly             -- ^ idx 9: コナジラミ
  deriving stock (Show, Eq, Ord, Enum, Bounded)

instance Pretty DiseaseIndex where
  pretty = \case
    Anthracnose       -> "Anthracnose"
    GrayMold          -> "Gray Mold"
    PowderyMildew     -> "Powdery Mildew"
    SpiderMite        -> "Spider Mite"
    Cutworm           -> "Cutworm"
    TobaccoBudworm    -> "Tobacco Budworm"
    CitrusThrips      -> "Citrus Thrips"
    CottonStinkbug    -> "Cotton Stinkbug"
    Aphid             -> "Aphid"
    Whitefly          -> "Whitefly"

------------------------------------------------------------------------------
-- Entry Vector (Demand Layer)
------------------------------------------------------------------------------

-- | An entry vector is a 0/1 vector over the disease dimension.
-- 1 = disease present (water flows), 0 = absent (blocked).
newtype EntryVector = EV (U.Vector Int)
  deriving newtype (Eq, Show)

mkEntryVector :: [Int] -> Maybe EntryVector
mkEntryVector xs
  | length xs == dim = Just $ EV $ U.fromList xs
  | otherwise        = Nothing
  where dim = fromEnum (maxBound :: DiseaseIndex) + 1

isEntryActive :: EntryVector -> DiseaseIndex -> Bool
isEntryActive (EV v) idx = v U.! fromEnum idx /= 0

vectorDim :: EntryVector -> Int
vectorDim (EV v) = U.length v

------------------------------------------------------------------------------
-- Evaluation Boxes (Bridge Layer)
------------------------------------------------------------------------------

-- | Unique identifier for an EVAL_BOX (e.g., EB-01, EB-23).
newtype EvalBoxId = EBId String
  deriving stock (Show, Eq, Ord)

instance Pretty EvalBoxId where
  pretty (EBId s) = s

-- | An evaluation box: a semantic cluster of disease combinations.
-- The vector IS the boundary — matching against it classifies meaning.
data EvalBox = EvalBox
  { ebId       :: EvalBoxId
  , ebVector   :: EntryVector
  , ebName     :: String
  } deriving stock (Show, Eq)

mkEvalBox :: EvalBoxId -> EntryVector -> String -> Maybe EvalBox
mkEvalBox eid ev _name
  | vectorDim ev == vectorDim ebDefault = Just EvalBox {..}
  | otherwise                           = Nothing
  where ebDefault = EV (U.replicate (fromEnum (maxBound :: DiseaseIndex) + 1) 0)

------------------------------------------------------------------------------
-- Pesticides (SpecBridge Layer)
------------------------------------------------------------------------------

-- | Toxicity classification — a discrete semantic boundary.
data ToxicityClass
  = NonToxic      -- ^ 毒物・害獣鳥獣忌避剤以外
  | Toxic         -- ^ 毒物
  | HighlyToxic   -- ^ 劇物 (attenuated at L6)
  deriving stock (Show, Eq, Ord)

-- | Whether a pesticide participates in rotation management.
data RotationCategory
  = ChemicalSystem String  -- ^ e.g., \"QoI\", \"DMI\", \"ジアミド\"
  | NonRotation          -- ^ MIX, PHYSICAL — no rotation tracking
  deriving stock (Show, Eq)

-- | A pesticide: its target vector + usage constraints.
-- This is the \"meaning boundary\" for a chemical agent.
data Pesticide = Pesticide
  { pid            :: PesticideId
  , pname          :: String
  , targetVector   :: EntryVector   -- ^ Which diseases this pesticide targets
  , maxApplications :: Int           -- ^ Annual application limit (Infinity = unlimited)
  , phiDays        :: Int            -- ^ Pre-harvest interval (days)
  , toxicityClass  :: ToxicityClass
  , systemCode     :: String         -- ^ FRAC code or \"MIX\"/\"PHYSICAL\"
  , system         :: String         -- ^ Human-readable system name
  , mixingBanTargets :: [String]     -- ^ What this pesticide bans mixing with
  } deriving stock (Show, Eq)

newtype PesticideId = PID String
  deriving stock (Show, Eq, Ord)

instance Pretty PesticideId where
  pretty (PID s) = s

-- | Result of TARGET_MATRIX × entryVector for a single pesticide.
-- The number of overlapping disease dimensions.
newtype TargetMatch = TM Int
  deriving newtype (Show, Eq, Ord)

------------------------------------------------------------------------------
-- Bridge Weights (Reflect Layer primitives)
------------------------------------------------------------------------------

-- | What a BRIDGE gate does to water flow.
-- This is the algebraic replacement for if/else:
--   if (condition) then FullBlock else if (partial) then Attenuate w else FullPass
data WeightAction
  = FullPass              -- ^ weight = 1.0 (water flows unchanged)
  | FullBlock             -- ^ weight = 0.0 (complete cutoff)
  | Attenuate Double      -- ^ 0 < weight < 1 (partial reduction)
  deriving stock (Show, Eq)

-- | A bridge weight is a uniform vector applied via Hadamard product.
-- By convention, all dimensions get the same weight — the gate opens/closes
-- the entire line, not individual dimensions.
newtype BridgeWeight = BW Double
  deriving newtype (Show, Eq, Ord)

bridgeWeightValue :: BridgeWeight -> Double
bridgeWeightValue (BW w) = w

uniformWeight :: WeightAction -> Int -> BridgeWeight
uniformWeight action dim = BW $ case action of
  FullPass     -> 1.0
  FullBlock    -> 0.0
  Attenuate w  -> w

------------------------------------------------------------------------------
-- Bridge (Reflect Layer gates)
------------------------------------------------------------------------------

-- | Unique identifier for a SPEC-BRIDGE gate.
newtype BridgeId = BID String
  deriving stock (Show, Eq, Ord)

instance Pretty BridgeId where
  pretty (BID s) = s

-- | A BRIDGE gate: a conditional weight function over a pipeline.
--
-- The algebraic structure here is critical. Each bridge is defined by:
--   1. Its level (strictly increasing order — enforced by construction)
--   2. Its direction (ForwardOnly — invariant, not a variable)
--   3. A pure function from context → weight action
--
-- The \"if/else\" of the original code lives ENTIRELY inside
-- weightFn as a pattern match on BridgeContext. The engine itself
-- performs zero branching beyond the loop.
data Bridge = Bridge
  { bid          :: BridgeId
  , bLevel       :: Double          -- ^ Passing order (strictly increasing)
  , bDirection   :: ForwardOnly     -- ^ Reverse-valve constraint (always Forward)
  , bWeightFn    :: BridgeContext -> WeightAction
  , bReasonFn    :: BridgeContext -> String  -- ^ Reason text when blocked
  , bPenalty     :: Maybe (String, Double)   -- ^ (axis, delta) for scoring
  , bWarningFn   :: BridgeContext -> String  -- ^ Warning text when attenuated
  , bDescription :: String
  }

instance Show Bridge where
  show b = "Bridge { bid = " ++ show (bid b)
         ++ ", bLevel = " ++ show (bLevel b)
         ++ ", bDescription = " ++ show (bDescription b)
         ++ " }"

-- | Direction is an invariant — always Forward.
-- This is a phantom type that proves no backward flow exists.
data ForwardOnly = ForwardOnly deriving stock (Show, Eq)

-- | Domain-specific context passed to bridge weight/reason functions.
data BridgeContext = BridgeContext
  { bcPesticide    :: Pesticide
  , bcEntryVector  :: EntryVector
  , bcTargetMatch  :: TargetMatch
  , bcUsageState   :: Map.Map PesticideId Int   -- ^ Applications per pesticide this year
  , bcLastSprayDate :: Maybe Int   -- ^ YYYYMMDD or Nothing
  , bcLastPesticideIds :: [PesticideId]
  , bcLastPesticides   :: [Pesticide]   -- ^ Last sprayed pesticides (for mixing check)
  , bcIntervalDays :: Maybe Int             -- ^ Days since last spray
  , bcRotationState :: Map.Map String Int        -- ^ Continuous uses per system
  } deriving stock (Show, Eq)

-- | Trace entry for one bridge passage.
data BridgeTrace = BridgeTrace
  { btBridgeId   :: BridgeId
  , btLevel      :: Double
  , btWeight     :: BridgeWeight
  , btPassed     :: Bool
  , btAttenuated :: Bool
  } deriving stock (Show, Eq)

------------------------------------------------------------------------------
-- Flow through bridges
------------------------------------------------------------------------------

-- | The state of water flow after passing through bridges.
data FlowState
  = Flowing                              -- ^ Water reached the end
  | Blocked BridgeId String              -- ^ Blocked at a specific bridge
  deriving stock (Show, Eq)

isBlocked :: FlowState -> Bool
isBlocked Blocked{} = True
isBlocked Flowing   = False

isFlowing :: FlowState -> Bool
isFlowing Flowing = True
isFlowing Blocked{} = False

-- | Complete result of running a line through all bridges.
data FlowResult = FlowResult
  { frFlow       :: EntryVector    -- ^ Final flow vector (zero if blocked)
  , frState      :: FlowState
  , frTrace      :: [BridgeTrace]
  } deriving stock (Show, Eq)

------------------------------------------------------------------------------
-- Prescription (Spec Layer)
------------------------------------------------------------------------------

-- | Score breakdown across the three axes.
data ScoreBreakdown = ScoreBreakdown
  { sbEffectiveness :: Double   -- ^ mirrorId × 10 + coverageRatio × 5
  , sbSafety        :: Double   -- ^ Base 20 minus PHI/toxicity penalties
  , sbResistance    :: Double   -- ^ Base 15 minus rotation penalties + combo bonus
  } deriving stock (Show, Eq)

-- | A scored prescription set (1 or 2 pesticides).
data PrescriptionSet = PrescriptionSet
  { psPesticides    :: [Pesticide]
  , psIsCombo       :: Bool
  , psMatchCount    :: Int
  , psCoverageRatio :: Double
  , psMirrorId      :: Double
  , psEffectiveness :: Double
  , psSafety        :: Double
  , psResistance    :: Double
  , psTotalScore    :: Double
  , psWarnings      :: [String]
  , psBreakdown     :: ScoreBreakdown
  } deriving stock (Show, Eq)

-- | Overall prescription result.
data PrescriptionStatus
  = Success                        -- ^ Best set found
  | NoPesticideDefined [Int]       -- ^ No pesticide targets any active disease
  | AllBlockedByConstraints        -- ^ Pesticides exist but all blocked by L2-L6
  deriving stock (Show, Eq)

data PrescriptionResult = PrescriptionResult
  { prBest           :: Maybe PrescriptionSet
  , prAlternatives   :: [PrescriptionSet]
  , prExcludedSets   :: [(Pesticide, [String])]
  , prExcludedIndiv  :: [(Pesticide, [String])]
  , prBridgeTrace    :: [BridgeTrace]
  , prStatus         :: PrescriptionStatus
  } deriving stock (Show, Eq)

------------------------------------------------------------------------------
-- Typeclass for pretty-printing
------------------------------------------------------------------------------

class Pretty a where
  pretty :: a -> String

------------------------------------------------------------------------------
-- Vector utilities (unboxed, no external deps beyond base)
------------------------------------------------------------------------------

-- | Dot product of two integer vectors.
dotProductInt :: U.Vector Int -> U.Vector Int -> Int
dotProductInt a b = U.sum (U.zipWith (*) a b)

-- | Dot product returning Double (for scoring).
dotProduct :: U.Vector Int -> U.Vector Int -> Double
dotProduct a b = fromIntegral $ U.sum (U.zipWith (*) a b)

-- | L2 norm of an integer vector.
norm :: U.Vector Int -> Double
norm v = sqrt $ fromIntegral $ U.sum (U.map (^2) v)

-- | Cosine similarity between two 0/1 vectors.
-- mirror(c, x) = union(c) · x / (||union(c)|| × ||x||)
cosineSimilarity :: U.Vector Int -> U.Vector Int -> Double
cosineSimilarity a b =
  let na = norm a
      nb = norm b
  in if na == 0 || nb == 0
     then 0.0
     else dotProduct a b / (na * nb)

-- | Element-wise minimum (logical AND for 0/1 vectors).
vectorMin :: U.Vector Int -> U.Vector Int -> U.Vector Int
vectorMin = U.zipWith min

-- | Check if a vector is all zeros.
isZeroVector :: U.Vector Int -> Bool
isZeroVector = U.all (== 0)

-- | Hadamard product (element-wise multiplication).
hadamard :: U.Vector Int -> U.Vector Double -> U.Vector Int
hadamard a w = U.map (\(x, w') -> round (fromIntegral x * w')) (U.zip a w)

-- | Convert EntryVector to int vector for computation.
evToIntVector :: EntryVector -> U.Vector Int
evToIntVector (EV v) = v

-- | Convert EntryVector to double vector for computation.
evToDouble :: EntryVector -> U.Vector Double
evToDouble (EV v) = U.map fromIntegral v

-- | Count active (non-zero) dimensions.
countActive :: EntryVector -> Int
countActive (EV v) = U.length $ U.filter (/= 0) v
