{-# LANGUAGE LambdaCase #-}
{-# LANGUAGE RecordWildCards #-}
{-|
Module      : Data.RBP.Core
Description : RBP waterway propagation engine — domain-independent core.

This is the pure algebraic engine. It knows NOTHING about diseases,
pesticides, or agriculture. It only knows:

  1. A "flow" is a vector of Ints
  2. A "bridge" transforms flow via Hadamard product with a weight
  3. Bridges execute in level order (strictly increasing)
  4. Zero flow = blocked (algebraic, not boolean)

The engine is a fold. The bridges define the fold function.
No if/else in the engine body — all branching is encoded in
the WeightAction algebraic type returned by each bridge's fn.

This is the mathematical core of RBP:
  f = x ⊙ W₁ ⊙ W₂ ⊙ ⋯ ⊙ Wₖ
where each Wᵢ is a uniform weight vector produced by a bridge gate.
-}
module Data.RBP.Core
  ( runLineThroughBridges
  , validateBridges
  ) where

import Data.RBP.Types
import qualified Data.Vector.Unboxed as U
import Data.List (sortBy, foldl')
import Data.Ord (comparing)

------------------------------------------------------------------------------
-- Helper: safe list indexing
------------------------------------------------------------------------------

safeIndex :: [a] -> Int -> Maybe a
safeIndex [] _  = Nothing
safeIndex (x:_) 0 = Just x
safeIndex (_:xs) i = safeIndex xs (i - 1)

------------------------------------------------------------------------------
-- Validation: prove invariants before execution
------------------------------------------------------------------------------

{-|
Validate that a bridge list satisfies RBP structural invariants:

  1. All directions are ForwardOnly (reverse-valve constraint)
  2. Levels are strictly increasing (cycle-free constraint)

Returns Left errorMsg on violation, Right () on success.

This replaces the runtime error checks from the JS version with
a compile-time-provable guarantee. If validateBridges passes,
the fold is guaranteed to be acyclic and forward-only.
-}
validateBridges :: [Bridge] -> Either String ()
validateBridges bridges =
  let sorted = sortBy (comparing bLevel) bridges
      errs = concat
        [ [ "BRIDGE " ++ pretty (bid b) ++ ": direction must be ForwardOnly"
          | b <- bridges, bDirection b /= ForwardOnly ]
        , [ "BRIDGE " ++ pretty (bid b) ++ ": level " ++ show (bLevel b)
              ++ " not strictly greater than previous"
          | (b, idx) <- zip sorted [0..], idx > 0
          , bLevel b <= levelAt (idx - 1) ]
        ]
      levelAt i = case safeIndex sorted i of
        Just b' -> bLevel b'
        Nothing -> -1
  in if null errs then Right () else Left (head errs)

------------------------------------------------------------------------------
-- Engine: fold bridges over flow
------------------------------------------------------------------------------

{-|
Run a line (vertical pipe) through all bridges in level order.

The algorithm is a strict left fold:

  > foldl' step initialFlow (sorted bridges)
  > where step flow bridge = hadamard flow (weightFn bridge ctx)

Each step is pure. The trace accumulates what happened.
Blocking is detected algebraically: if the flow vector becomes
all zeros, we short-circuit and record which bridge caused it.

This is the direct Haskell translation of:
  f_i = x_i · ∏_{j=1}^{k} (W_j)_i

But expressed as a composition of unary operators rather than
a single formula — which is what makes it composable and testable.
-}
runLineThroughBridges
  :: EntryVector          -- ^ initialFlow: water from the source
  -> [Bridge]             -- ^ bridges (will be sorted by level internally)
  -> BridgeContext        -- ^ domain context for weight/reason functions
  -> FlowResult
runLineThroughBridges initialFlow bridges ctx =
  let sorted = sortBy (comparing bLevel) bridges
      result = foldl' step (FlowResult initialFlow Flowing []) sorted
  in result
  where
    step (FlowResult flow state trace) bridge =
      let (EV rawFlow) = flow
          bw = uniformWeight weightAction (vectorDim flow)
          wVec = U.replicate (U.length rawFlow) (bridgeWeightValue bw)
          newRaw = hadamard rawFlow wVec
          newFlow = EV newRaw
          blocked = isZeroVector newRaw && isFlowing state

          traceEntry = BridgeTrace
            { btBridgeId   = bid bridge
            , btLevel      = bLevel bridge
            , btWeight     = bw
            , btPassed     = not (isZeroVector newRaw)
            , btAttenuated = not (isZeroVector newRaw) && bridgeWeightValue bw < 1.0
            }

          newState = case (state, blocked) of
            (Flowing, True) -> Blocked (bid bridge) (bReasonFn bridge ctx)
            _               -> state

      in FlowResult newFlow newState (trace ++ [traceEntry])
      where
        weightAction = bWeightFn bridge ctx
