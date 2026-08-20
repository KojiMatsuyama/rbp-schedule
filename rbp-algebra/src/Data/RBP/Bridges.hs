{-# LANGUAGE LambdaCase #-}
{-# LANGUAGE RecordWildCards #-}
{-|
Module      : Data.RBP.Bridges
Description : The 6-stage SPEC-BRIDGE waterway definition.

This module defines the 6 BRIDGE gates that form the Reflect layer.
Each gate is a pure function from BridgeContext → WeightAction.

The algebraic structure:

  EB_VECTOR (source)
    → SPEC_LINE (per-pesticide vertical pipes)
    → L1 SPEC-BRIDGE-TARGET     target mismatch → FullBlock
    → L2 SPEC-BRIDGE-USAGE      usage limit     → FullBlock
    → L3 SPEC-BRIDGE-PHI        PHI insufficient → Attenuate 0.5
    → L4 SPEC-BRIDGE-ROTATION   rotation abuse   → Attenuate 0.3
    → L5 SPEC-BRIDGE-MIXING     mixing banned    → FullBlock
    → L6 SPEC-BRIDGE-TOXICITY   highly toxic     → Attenuate 0.7
    → flowing lines → set enumeration → SPEC-BRIDGE-MIXING-SET → SPEC_BOX

Each bridge is defined declaratively. The \"business logic\" (which
conditions trigger blocking vs attenuation) lives in the weightFn
as a pattern match on BridgeContext — NOT in the engine loop.

This is the key proof point: procedural if/else chains become
declarative algebraic transformations.
-}
module Data.RBP.Bridges
  ( specBridges
  , runSpecLine
  , runAllSpecLines
  , hasMixingConflict
  , setHasInternalMixingConflict
  , buildMixingReason
  ) where

import Data.RBP.Types
import Data.RBP.Core (runLineThroughBridges)
import qualified Data.Map.Strict as Map
import Data.List (sortBy)
import Data.Ord (comparing)
import Data.Char (toLower)

------------------------------------------------------------------------------
-- Bridge helper constructors
------------------------------------------------------------------------------

fullPass :: Int -> WeightAction
fullPass _ = FullPass

fullBlock :: Int -> WeightAction
fullBlock _ = FullBlock

attenuate :: Double -> Int -> WeightAction
attenuate w _ = Attenuate w

------------------------------------------------------------------------------
-- The 6 SPEC-BRIDGES
------------------------------------------------------------------------------

{-|
All 6 SPEC-BRIDGES in level order.

Each bridge is a record with:
  - id, level, direction (structural invariants)
  - weightFn: the business logic as a pure function
  - reasonFn: human-readable explanation when blocked
  - penalty: (axis, delta) for scoring when attenuated
  - warningFn: human-readable warning when attenuated
  - description: documentation

The weightFn is where the \"if/else\" of the original code lives —
but as a pattern match on BridgeContext, not as control flow in the engine.
-}
specBridges :: [Bridge]
specBridges =
  [ -- L1: Target matching — does this pesticide target any active disease?
    Bridge
      { bid = BID "SPEC-BRIDGE-TARGET"
      , bLevel = 1.0
      , bDirection = ForwardOnly
      , bWeightFn = \ctx ->
          if targetMatchCtx ctx > 0
            then fullPass (vectorDim (bcEntryVector ctx))
            else fullBlock (vectorDim (bcEntryVector ctx))
      , bReasonFn = \ctx ->
          pname (bcPesticide ctx) ++ ": target disease does not match entry vector"
      , bPenalty = Nothing
      , bWarningFn = \_ -> ""
      , bDescription = "Blocks pesticides whose target diseases don't overlap with entry vector"
      }

  , -- L2: Usage limit — annual application count exceeded?
    Bridge
      { bid = BID "SPEC-BRIDGE-USAGE"
      , bLevel = 2.0
      , bDirection = ForwardOnly
      , bWeightFn = \ctx ->
          let usageCount = Map.findWithDefault 0 (pid (bcPesticide ctx)) (bcUsageState ctx)
              lim = maxApplications (bcPesticide ctx)
              limitReached = lim /= -1 && usageCount >= lim  -- -1 = Infinity
          in if limitReached
               then fullBlock (vectorDim (bcEntryVector ctx))
               else fullPass (vectorDim (bcEntryVector ctx))
      , bReasonFn = \ctx ->
          let usageCount = Map.findWithDefault 0 (pid (bcPesticide ctx)) (bcUsageState ctx)
              lim = maxApplications (bcPesticide ctx)
          in "Application limit reached (" ++ show usageCount ++ "/" ++
             if lim == -1 then "unlimited" else show lim ++ ")"
      , bPenalty = Nothing
      , bWarningFn = \_ -> ""
      , bDescription = "Blocks pesticides that have reached their annual application limit"
      }

  , -- L3: PHI (Pre-Harvest Interval) — harvest too soon after last spray?
    Bridge
      { bid = BID "SPEC-BRIDGE-PHI"
      , bLevel = 3.0
      , bDirection = ForwardOnly
      , bWeightFn = \ctx ->
          case (bcIntervalDays ctx, phiDays (bcPesticide ctx)) of
            (Just days, phi) ->
              if days < phi
                then attenuate 0.5 (vectorDim (bcEntryVector ctx))
                else fullPass (vectorDim (bcEntryVector ctx))
            (Nothing, _) ->
              fullPass (vectorDim (bcEntryVector ctx))  -- no history = safe
      , bReasonFn = \_ -> "PHI check: not a blocker, only attenuator"
      , bPenalty = Just ("safety", -10.0)
      , bWarningFn = \ctx ->
          case bcIntervalDays ctx of
            Just days -> pname (bcPesticide ctx) ++ ": PHI residual days check required (" ++
                         show days ++ " days since last spray, PHI " ++
                         show (phiDays (bcPesticide ctx)) ++ " days)"
            Nothing -> ""
      , bDescription = "Attenuates pesticides where PHI residual days are insufficient (not a hard block)"
      }

  , -- L4: Rotation management — same system used 2+ times consecutively?
    Bridge
      { bid = BID "SPEC-BRIDGE-ROTATION"
      , bLevel = 4.0
      , bDirection = ForwardOnly
      , bWeightFn = \ctx ->
          let sysCode = systemCode (bcPesticide ctx)
              isNonRotation = sysCode `elem` ["MIX", "PHYSICAL"]
              rotCount = Map.findWithDefault 0 sysCode (bcRotationState ctx)
              abuse = rotCount >= 2 && not isNonRotation
          in if abuse
               then attenuate 0.3 (vectorDim (bcEntryVector ctx))
               else fullPass (vectorDim (bcEntryVector ctx))
      , bReasonFn = \ctx ->
          let sysCode = systemCode (bcPesticide ctx)
              rotCount = Map.findWithDefault 0 sysCode (bcRotationState ctx)
          in pname (bcPesticide ctx) ++ ": same system (" ++ sysCode ++ ") used " ++
             show rotCount ++ " times consecutively (resistance risk)"
      , bPenalty = Just ("resistance", -15.0)
      , bWarningFn = \ctx ->
          let sysCode = systemCode (bcPesticide ctx)
              rotCount = Map.findWithDefault 0 sysCode (bcRotationState ctx)
          in pname (bcPesticide ctx) ++ ": same system (" ++ system (bcPesticide ctx) ++
             ") used " ++ show rotCount ++ " times consecutively (resistance risk)"
      , bDescription = "Attenuates pesticides in systems with excessive consecutive use (resistance management)"
      }

  , -- L5: Mixing compatibility — conflicts with last sprayed pesticide?
    Bridge
      { bid = BID "SPEC-BRIDGE-MIXING"
      , bLevel = 5.0
      , bDirection = ForwardOnly
      , bWeightFn = \ctx ->
          let lastPests = bcLastPesticides ctx
              conflicts = any (hasMixingConflict (bcPesticide ctx)) lastPests
          in if conflicts
               then fullBlock (vectorDim (bcEntryVector ctx))
               else fullPass (vectorDim (bcEntryVector ctx))
      , bReasonFn = \ctx ->
          pname (bcPesticide ctx) ++ " cannot mix with last sprayed pesticides"
      , bPenalty = Nothing
      , bWarningFn = \_ -> ""
      , bDescription = "Blocks pesticides that conflict with the last sprayed pesticide"
      }

  , -- L6: Toxicity class — highly toxic (劇物)?
    Bridge
      { bid = BID "SPEC-BRIDGE-TOXICITY"
      , bLevel = 6.0
      , bDirection = ForwardOnly
      , bWeightFn = \ctx ->
          let isHighlyToxic = toxicityClass (bcPesticide ctx) == HighlyToxic
          in if isHighlyToxic
               then attenuate 0.7 (vectorDim (bcEntryVector ctx))
               else fullPass (vectorDim (bcEntryVector ctx))
      , bReasonFn = \_ -> "Toxicity check: not a blocker, only attenuator"
      , bPenalty = Just ("safety", -8.0)
      , bWarningFn = \ctx ->
          pname (bcPesticide ctx) ++ ": highly toxic classification"
      , bDescription = "Attenuates highly toxic pesticides (discouraged but not prohibited)"
      }
  ]

------------------------------------------------------------------------------
-- Running specs
------------------------------------------------------------------------------

{-|
Run a single pesticide's SPEC_LINE through all 6 bridges.
-}
runSpecLine
  :: Pesticide
  -> EntryVector
  -> BridgeContext
  -> FlowResult
runSpecLine pesticide ebVector ctx =
  let ctx' = ctx { bcPesticide = pesticide }
  in runLineThroughBridges ebVector specBridges ctx'

{-|
Run all pesticides through their SPEC_LINEs.
-}
runAllSpecLines
  :: [Pesticide]
  -> EntryVector
  -> BridgeContext
  -> [FlowResult]
runAllSpecLines pesticides ebVector ctx =
  map (\p -> runSpecLine p ebVector ctx { bcPesticide = p }) pesticides

------------------------------------------------------------------------------
-- Mixing conflict helpers
------------------------------------------------------------------------------

{-|
Check if two pesticides have a mixing conflict.
Two pesticides conflict if either bans mixing with the other's system or name.
-}
hasMixingConflict :: Pesticide -> Pesticide -> Bool
hasMixingConflict a b =
  let aBans = mixingBanTargets a
      bBans = mixingBanTargets b
      bSys = system b
      bNm = pname b
      aSys = system a
      aNm = pname a
  in (any (\t -> bSys == t || t `isInfixOf` bSys || bNm == t || t `isInfixOf` bNm) aBans) ||
     (any (\t -> aSys == t || t `isInfixOf` aSys || aNm == t || t `isInfixOf` aNm) bBans)

{-|
Check if a set of pesticides has internal mixing conflicts.
Only applies to pairs (2-element sets).
-}
setHasInternalMixingConflict :: [Pesticide] -> Bool
setHasInternalMixingConflict [a, b] = hasMixingConflict a b
setHasInternalMixingConflict _      = False

{-|
Build human-readable mixing-conflict reason strings between two pesticides.
Mirrors the Python reference engine's main.py:_build_mixing_reason /
_mentions exactly (haystack = ban-target string, needle = name/system) —
note this argument order is intentionally the reverse of 'hasMixingConflict'
above, matching a pre-existing asymmetry in the Python reference itself.
-}
buildMixingReason :: Pesticide -> Pesticide -> [String]
buildMixingReason a b =
  concat
    [ [ pname a ++ "は" ++ pname b ++ "（" ++ system b ++ "）と混用不可"
      | any (\t -> mentions t (system b) || mentions t (pname b)) (mixingBanTargets a) ]
    , [ pname b ++ "は" ++ pname a ++ "（" ++ system a ++ "）と混用不可"
      | any (\t -> mentions t (system a) || mentions t (pname a)) (mixingBanTargets b) ]
    ]
  where
    mentions haystack needle =
      needle `isInfixOf` haystack || map toLower needle `isInfixOf` map toLower haystack

------------------------------------------------------------------------------
-- Helpers
------------------------------------------------------------------------------

lookupPid :: PesticideId -> Pesticide
lookupPid _ = error "lookupPid: not implemented — mixing check uses direct Pesticide comparison"

targetMatchCtx :: BridgeContext -> Int
targetMatchCtx (BridgeContext { bcTargetMatch = TM m }) = m

isInfixOf :: String -> String -> Bool
isInfixOf needle haystack = any (isPrefixOf needle) (tails haystack)

isPrefixOf :: String -> String -> Bool
isPrefixOf [] _ = True
isPrefixOf _ [] = False
isPrefixOf (x:xs) (y:ys) = x == y && isPrefixOf xs ys

tails :: String -> [String]
tails [] = [""]
tails s@( _:rest) = s : tails rest
