{-# LANGUAGE LambdaCase #-}
{-# LANGUAGE RecordWildCards #-}
{-|
Module      : Main
Description : RBP Algebraic Engine — production data driven.

Runs the full 5-layer RBP pipeline using real agricultural data
(67 pesticides from data/pesticides.json, 20 EVAL_BOXes from
data/eval_boxes.json).

Pipeline:
  1. Demand  : Create an EntryVector from disease observations
  2. Bridge  : Match against EVAL_BOXes (meaning-set classification)
  3. SpecBridge: Compute target matches via TARGET_MATRIX x entryVector
  4. Reflect : Run 6-stage BRIDGE waterway (constraint propagation)
  5. Spec    : Select optimal prescription set via Mirror-ID optimisation

Run with: cabal run rbp-algebra
Run JSON API: cabal run rbp-algebra -- --prescribe 0,1,1,1,0,1,0,0,0,0
-}
module Main where

import Data.RBP.Types
import Data.RBP.Core (runLineThroughBridges)
import Data.RBP.Bridges (specBridges, setHasInternalMixingConflict, buildMixingReason)
import Data.RBP.DataLoader (loadPesticides, loadEvalBoxes, samplePesticidesFallback, sampleEvalBoxesFallback)
import qualified Data.Map.Strict as Map
import Data.List (sortBy, nub, intercalate, find)
import Data.Ord (Down (..), comparing)
import qualified Data.Vector.Unboxed as U
import System.Environment (getArgs)
import System.IO (hSetEncoding, stdout, utf8)
import Text.Read (readMaybe)

------------------------------------------------------------------------------
-- Helper: unsafe from list (safe because we control the literals)
------------------------------------------------------------------------------

ev :: [Int] -> EntryVector
ev xs = case mkEntryVector xs of
  Just v  -> v
  Nothing -> error $ "ev: expected 10 ints, got " ++ show (length xs)

------------------------------------------------------------------------------
-- Example: EB-23 scenario from 解説2
------------------------------------------------------------------------------

exampleEntryVector :: EntryVector
exampleEntryVector = ev [0,1,1,1,0,1,0,0,0,0]

------------------------------------------------------------------------------
-- Empty safety context (no usage history - the "clean slate" scenario)
------------------------------------------------------------------------------

emptySafetyCtx :: BridgeContext
emptySafetyCtx =
  BridgeContext
    { bcPesticide    = head samplePesticidesFallback
    , bcEntryVector  = exampleEntryVector
    , bcTargetMatch  = TM 0
    , bcUsageState   = Map.empty
    , bcLastSprayDate = Nothing
    , bcLastPesticideIds = []
    , bcLastPesticides   = []
    , bcIntervalDays   = Nothing
    , bcRotationState  = Map.empty
    }

------------------------------------------------------------------------------
-- Core prescription logic (data-driven)
------------------------------------------------------------------------------

computeTargetMatches :: [Pesticide] -> EntryVector -> [(Pesticide, TargetMatch)]
computeTargetMatches pesticides ev =
  map (\p -> (p, TM $ countOverlap (targetVector p) ev)) pesticides

countOverlap :: EntryVector -> EntryVector -> Int
countOverlap (EV a) (EV b) = U.sum $ U.zipWith min a b

computeUnionCoverage :: [Pesticide] -> EntryVector -> EntryVector
computeUnionCoverage pesticides ev =
  let dim = vectorDim ev
      indices = [0 .. dim - 1]
      unionVec = U.fromList
        [ if any (\p -> evToIntVector (targetVector p) U.! i > 0) pesticides then 1 else 0
        | i <- indices ]
  in EV unionVec

scorePrescriptionSet :: [Pesticide] -> EntryVector -> Double
scorePrescriptionSet pesticides ev =
  let unionVec = computeUnionCoverage pesticides ev
      matchCount = fromIntegral $ dotProductInt (evToIntVector unionVec) (evToIntVector ev)
      targetSum  = fromIntegral $ countActive ev
      coverageRatio = if targetSum > 0 then matchCount / fromIntegral targetSum else 0
      mirrorId = cosineSimilarity (evToIntVector unionVec) (evToIntVector ev)
      effectiveness = mirrorId * 10 + coverageRatio * 5
      safetyBase = 20.0
      resistanceBase = 15.0
      total = effectiveness + safetyBase + resistanceBase
  in total

------------------------------------------------------------------------------
-- Full scoring breakdown (effectiveness / safety / resistance) for the
-- --prescribe JSON API — ports Python's rbp-algebra-python/main.py's
-- _score_set_full. scorePrescriptionSet above is left as-is for runDemo.
------------------------------------------------------------------------------

data Breakdown = Breakdown
  { bdEffectiveness  :: Double
  , bdMirrorId       :: Double
  , bdCoverageRatio  :: Double
  , bdMatchCount     :: Int
  , bdTargetSum      :: Int
  , bdSafety         :: Double
  , bdSafetyWarnings :: [String]
  , bdResistance     :: Double
  , bdResistanceNote :: String
  , bdTotalScore     :: Double
  }

findBridgeByLevel :: Double -> Maybe Bridge
findBridgeByLevel lvl = find (\b -> bLevel b == lvl) specBridges

scoreSetBreakdown
  :: [Pesticide]
  -> EntryVector
  -> Map.Map PesticideId FlowResult   -- ^ flowingMap: pid -> FlowResult, for pesticides that reached L6
  -> [(Pesticide, FlowResult)]        -- ^ pool: connected pesticides not blocked at USAGE (redundancy check)
  -> Breakdown
scoreSetBreakdown pesticides entryV flowingMap pool =
  let unionVec      = computeUnionCoverage pesticides entryV
      matchCount    = dotProductInt (evToIntVector unionVec) (evToIntVector entryV)
      targetSum     = countActive entryV
      coverageRatio = if targetSum > 0
                        then fromIntegral matchCount / fromIntegral targetSum
                        else 0 :: Double
      mirrorId      = cosineSimilarity (evToIntVector unionVec) (evToIntVector entryV)
      effectiveness = mirrorId * 10 + coverageRatio * 5

      penaltyEvents =
        [ (axis, delta, bWarningFn bridge (emptySafetyCtx { bcPesticide = p, bcEntryVector = entryV }))
        | p <- pesticides
        , Just fr <- [Map.lookup (pid p) flowingMap]
        , t <- frTrace fr, btAttenuated t
        , Just bridge <- [findBridgeByLevel (btLevel t)]
        , Just (axis, delta) <- [bPenalty bridge]
        ]
      safetyEvents     = [ (d, w) | (axis, d, w) <- penaltyEvents, axis == "safety" ]
      resistanceEvents = [ (d, w) | (axis, d, w) <- penaltyEvents, axis == "resistance" ]
      safetyWarnings   = map snd safetyEvents
      safetyScore      = max 0.0 (20.0 + sum (map fst safetyEvents))

      nonRotationCodes = ["MIX", "PHYSICAL"]
      (comboAdjustment, resistanceNote) = case pesticides of
        [a, b]
          | systemCode a `notElem` nonRotationCodes
          , systemCode b `notElem` nonRotationCodes ->
              let isSameSystem = systemCode a == systemCode b
                  isRedundant = any
                    (\(sp, _) ->
                       dotProductInt
                         (evToIntVector (computeUnionCoverage [sp] entryV))
                         (evToIntVector entryV)
                         >= matchCount)
                    pool
              in if isSameSystem
                   then (-20.0 :: Double, "同一系統の組み合わせ：抵抗性リスク低減効果なし")
                   else if not isRedundant
                          then (0.0, "異なる系統（" ++ systemCode a ++ "／" ++ systemCode b ++ "）の組み合わせ：抵抗性管理上有効")
                          else (0.0, "")
        _ -> (0.0, "")
      resistanceScore = max 0.0 (15.0 + sum (map fst resistanceEvents) + comboAdjustment)
      totalScore      = effectiveness + safetyScore + resistanceScore
  in Breakdown
       { bdEffectiveness  = effectiveness
       , bdMirrorId       = mirrorId
       , bdCoverageRatio  = coverageRatio
       , bdMatchCount     = matchCount
       , bdTargetSum      = targetSum
       , bdSafety         = safetyScore
       , bdSafetyWarnings = safetyWarnings
       , bdResistance     = resistanceScore
       , bdResistanceNote = resistanceNote
       , bdTotalScore     = totalScore
       }

matchEvalBox :: EntryVector -> [EvalBox] -> Either String (Maybe EvalBoxId)
matchEvalBox ev boxes =
  let matches = filter (exactMatch ev) boxes
  in case matches of
       []    -> Right Nothing
       [b]   -> Right (Just (ebId b))
       _:_   -> Left "MODEL_DEFINITION_ERROR: multiple eval box matches"

exactMatch :: EntryVector -> EvalBox -> Bool
exactMatch (EV a) (EvalBox _ (EV b) _) = a == b

------------------------------------------------------------------------------
-- Pretty printing
------------------------------------------------------------------------------

sep :: String
sep = replicate 72 '='

section :: String -> IO ()
section title = do
  putStrLn ""
  putStrLn sep
  putStrLn ("  " ++ title)
  putStrLn sep

fmtFR :: FlowResult -> String
fmtFR fr =
  let stateStr = case frState fr of
        Flowing       -> "FLOWING [ok]"
        Blocked bid _ -> "BLOCKED [x] at " ++ pretty bid
      traceLines = map (\t ->
        "    L" ++ show (btLevel t) ++ ": " ++ pretty (btBridgeId t) ++
        " w=" ++ show (btWeight t) ++
        " | " ++ if btPassed t then "pass" else "stop"
        ++ if btAttenuated t then " (attenuated)" else "") (frTrace fr)
  in "  State: " ++ stateStr ++ "\n" ++ unlines traceLines

padRight :: Int -> String -> String
padRight n s = s ++ take (n - length s) (repeat ' ')

------------------------------------------------------------------------------
-- JSON API mode (--prescribe)
-- Hand-built JSON (no aeson dependency for output).
------------------------------------------------------------------------------

jsonEscape :: String -> String
jsonEscape = concatMap esc
  where
    esc '"'  = "\\\""
    esc '\\' = "\\\\"
    esc '\n' = "\\n"
    esc '\r' = "\\r"
    esc '\t' = "\\t"
    esc c    = [c]

jStr :: String -> String
jStr s = "\"" ++ jsonEscape s ++ "\""

jObj :: [(String, String)] -> String
jObj kvs = "{" ++ intercalate "," [ jStr k ++ ":" ++ v | (k, v) <- kvs ] ++ "}"

jArr :: [String] -> String
jArr xs = "[" ++ intercalate "," xs ++ "]"

-- | Raw JSON number (no quotes) — for numeric fields the frontend calls .toFixed() on.
jNum :: Show a => a -> String
jNum n = show n

jBool :: Bool -> String
jBool b = if b then "true" else "false"

parseCsvInts :: String -> [Int]
parseCsvInts s =
  let ws = words (map (\c -> if c == ',' then ' ' else c) s)
  in [ n | w <- ws, Just n <- [readMaybe w] ]

isBlockedAt :: String -> FlowState -> Bool
isBlockedAt bidStr (Blocked bid' _) = pretty bid' == bidStr
isBlockedAt _      Flowing          = False

lineTraceJson :: (Pesticide, FlowResult) -> String
lineTraceJson (p, fr) = jObj
  [ ("pesticide", jStr (pretty (pid p)))
  , ("pesticide_name", jStr (pname p))
  , ("levels", jArr (map (jNum . btLevel) (frTrace fr)))
  , ("weights", jArr (map (jNum . bridgeWeightValue . btWeight) (frTrace fr)))
  , ("blocked", jBool (isBlocked (frState fr)))
  , ("blocked_at", case frState fr of
       Blocked bid' _ -> jStr (pretty bid')
       Flowing        -> "null")
  ]

excludedIndividualJson :: (Pesticide, BridgeId, String) -> String
excludedIndividualJson (p, bid', reason) = jObj
  [ ("pesticidePid", jStr (pretty (pid p)))
  , ("pesticideName", jStr (pname p))
  , ("bridgeId", jStr (pretty bid'))
  , ("reason", jStr reason)
  ]

excludedSetJson :: (Pesticide, Pesticide, [String]) -> String
excludedSetJson (a, b, reasons) = jObj
  [ ("pesticidePids", jArr [jStr (pretty (pid a)), jStr (pretty (pid b))])
  , ("pesticideNames", jArr [jStr (pname a), jStr (pname b)])
  , ("gateId", jStr "SPEC-BRIDGE-MIXING-SET")
  , ("reasons", jArr (map jStr reasons))
  ]

breakdownJson :: Breakdown -> String
breakdownJson bd = jObj
  [ ("effectiveness", jObj
      [ ("raw", jNum (bdEffectiveness bd))
      , ("mirrorId", jNum (bdMirrorId bd))
      , ("coverageRatio", jNum (bdCoverageRatio bd))
      , ("matchCount", jNum (bdMatchCount bd))
      , ("targetSum", jNum (bdTargetSum bd))
      ])
  , ("safety", jObj
      [ ("raw", jNum (bdSafety bd))
      , ("warnings", jArr (map jStr (bdSafetyWarnings bd)))
      ])
  , ("resistance", jObj
      [ ("raw", jNum (bdResistance bd))
      , ("note", jStr (bdResistanceNote bd))
      ])
  , ("mixingOk", jBool True)
  , ("mixingReasons", jArr [])
  ]

-- | Run prescription with given data sources.
prescribeWith :: EntryVector -> [Pesticide] -> [EvalBox] -> String
prescribeWith entryV pesticides evalBoxes =
  let mkCtx p = emptySafetyCtx
        { bcPesticide   = p
        , bcEntryVector = entryV
        , bcTargetMatch = TM (countOverlap (targetVector p) entryV)
        }
      lineResults = [ (p, runLineThroughBridges entryV specBridges (mkCtx p)) | p <- pesticides ]

      -- connected = passed L1 TARGET; flowing = connected AND reached L6; pool = connected minus USAGE-blocked
      connected = [ (p, fr) | (p, fr) <- lineResults, not (isBlockedAt "SPEC-BRIDGE-TARGET" (frState fr)) ]
      flowing   = [ (p, fr) | (p, fr) <- connected, not (isBlocked (frState fr)) ]
      pool      = [ (p, fr) | (p, fr) <- connected, not (isBlockedAt "SPEC-BRIDGE-USAGE" (frState fr)) ]

      flowingMap = Map.fromList [ (pid p, fr) | (p, fr) <- flowing ]

      excludedIndividual =
        [ (p, bid', reason) | (p, fr) <- connected, Blocked bid' reason <- [frState fr] ]

      flowingPesticides = map fst flowing
      pairSets   = [ [a, b] | (i, a) <- zip [(0 :: Int) ..] flowingPesticides, b <- drop (i + 1) flowingPesticides ]
      candidates = map (: []) flowingPesticides ++ pairSets

      classifySet s
        | [a, b] <- s, setHasInternalMixingConflict s = Left (a, b, buildMixingReason a b)
        | otherwise = Right s
      classified   = map classifySet candidates
      excludedSets = [ (a, b, rs) | Left (a, b, rs) <- classified ]
      validSets    = [ s | Right s <- classified ]

      sortKey (s, bd) =
        ( Down (bdMirrorId bd), Down (bdTotalScore bd), length s
        , intercalate "," (map (pretty . pid) s) )
      scored = sortBy (comparing sortKey)
                 [ (s, scoreSetBreakdown s entryV flowingMap pool) | s <- validSets ]

      ebJson = case matchEvalBox entryV evalBoxes of
        Right Nothing    -> "{\"status\": \"UNDEFINED\", \"detail\": null}"
        Right (Just eid) -> "{\"status\": \"MATCH\", \"detail\": \"" ++ pretty eid ++ "\"}"
        Left err         -> "{\"status\": \"ERROR\", \"detail\": \"" ++ jsonEscape err ++ "\"}"
      setJson (s, bd) = jObj
        [ ("pesticides", jArr [ jObj [ ("id", jStr (pretty (pid p)))
                                     , ("name", jStr (pname p))
                                     , ("system", jStr (system p)) ]
                               | p <- s ])
        , ("matchCount", jNum (bdMatchCount bd))
        , ("coverageRatio", jNum (bdCoverageRatio bd))
        , ("mirrorId", jNum (bdMirrorId bd))
        , ("totalScore", jNum (bdTotalScore bd))
        , ("breakdown", breakdownJson bd)
        ]
      (statusStr, bestJson, altsJson, lineTracesJ, exclIndivJ, exclSetsJ)
        | null connected = ( "NO_PESTICIDE_DEFINED", "null", jArr []
                            , jArr [], jArr [], jArr [] )
        | null flowing   = ( "ALL_BLOCKED_BY_CONSTRAINTS", "null", jArr []
                            , jArr []
                            , jArr (map excludedIndividualJson excludedIndividual)
                            , jArr [] )
        | null scored    = ( "ALL_BLOCKED_BY_CONSTRAINTS", "null", jArr []
                            , jArr (map lineTraceJson connected)
                            , jArr (map excludedIndividualJson excludedIndividual)
                            , jArr (map excludedSetJson excludedSets) )
        | otherwise      = ( "SUCCESS"
                            , setJson (head scored)
                            , jArr (map setJson (take 10 (drop 1 scored)))
                            , jArr (map lineTraceJson connected)
                            , jArr (map excludedIndividualJson excludedIndividual)
                            , jArr (map excludedSetJson excludedSets) )
  in jObj
       [ ("engine", jStr "haskell")
       , ("sampleDb", jBool False)
       , ("pesticideCount", jNum (length pesticides))
       , ("evalBox", ebJson)
       , ("status", jStr statusStr)
       , ("best", bestJson)
       , ("alternatives", altsJson)
       , ("lineTraces", lineTracesJ)
       , ("excludedIndividual", exclIndivJ)
       , ("excludedSets", exclSetsJ)
       ]

------------------------------------------------------------------------------
-- Main
------------------------------------------------------------------------------

main :: IO ()
main = do
  hSetEncoding stdout utf8
  args <- getArgs
  case args of
    -- JSON API mode: server.py POST /api/prescribe (engine=haskell)
    ["--prescribe", csv] -> do
      pesticides <- loadPesticides
      evalBoxes  <- loadEvalBoxes
      case mkEntryVector (parseCsvInts csv) of
        Just v  -> putStrLn (prescribeWith v pesticides evalBoxes)
        Nothing -> putStrLn (jObj [("error", jStr "entryVector must be 10 comma-separated 0/1 values")])
    -- Demo mode: show full pipeline with loaded data
    _ -> runDemo

------------------------------------------------------------------------------
-- Demo mode (default, no arguments)
------------------------------------------------------------------------------

runDemo :: IO ()
runDemo = do
  putStrLn $ "+`" ++ concat (replicate 70 "-")
  putStrLn "|  RBP ALGEBRAIC ENGINE — Production Data"
  putStrLn "|  Real PESTICIDE_DB (67 agents) + EVAL_BOX (20 categories)"
  putStrLn $ "+`" ++ concat (replicate 70 "-")

  -- Load real data
  pesticides <- loadPesticides
  evalBoxes  <- loadEvalBoxes
  putStrLn $ "|  Loaded: " ++ show (length pesticides) ++ " pesticides, "
            ++ show (length evalBoxes) ++ " eval boxes"

  -- ===== LAYER 1: DEMAND =====
  section "LAYER 1: DEMAND - EntryVector Generation"
  putStrLn "  Scenario (EB-20): Gray Mold + Powdery Mildew +"
  putStrLn "  Spider Mite + Tobacco Budworm simultaneously active"
  putStrLn $ "  Vector: " ++ show (U.toList (evToIntVector exampleEntryVector))
  putStrLn $ "  Active dimensions: " ++ show (countActive exampleEntryVector)

  -- ===== LAYER 2: BRIDGE =====
  section "LAYER 2: BRIDGE - EVAL_BOX Classification"
  putStrLn $ "  Checking against " ++ show (length evalBoxes) ++ " EVAL_BOX boundaries..."
  case matchEvalBox exampleEntryVector evalBoxes of
    Right Nothing   -> putStrLn "  Result: UNDEFINED - new EVAL_BOX boundary detected!"
    Right (Just eid) -> putStrLn $ "  Result: MATCH - " ++ pretty eid
    Left err         -> putStrLn $ "  Result: ERROR - " ++ err

  -- ===== LAYER 3: SPECBRIDGE =====
  section "LAYER 3: SPECBRIDGE - Target Matching"
  putStrLn "  Top 10 pesticides by target overlap:"
  let matches = computeTargetMatches pesticides exampleEntryVector
      topMatches = take 10 $ sortBy (comparing (Down . snd)) matches
  mapM_ (\(p, TM m) ->
    putStrLn $ "  " ++ padRight 25 (pname p) ++ "  " ++ show m
    ) topMatches

  -- ===== LAYER 4: REFLECT =====
  section "LAYER 4: REFLECT - 6-Stage BRIDGE Waterway"
  putStrLn $ "  Running all " ++ show (length pesticides) ++ " pesticides through the waterway"
  putStrLn "  (Clean slate: no usage history, no PHI constraints)"
  putStrLn ""

  let lineResults = [ (p, runLineThroughBridges exampleEntryVector specBridges
                       (emptySafetyCtx { bcPesticide = p
                                       , bcTargetMatch = TM (countOverlap (targetVector p) exampleEntryVector) }))
                    | p <- take 10 pesticides ]
  mapM_ (\(p, fr) -> do
    putStrLn $ "  --- " ++ pname p ++ " ---"
    putStrLn $ fmtFR fr
    putStrLn ""
    ) lineResults

  -- ===== LAYER 5: SPEC =====
  section "LAYER 5: SPEC - Prescription Set Selection"
  putStrLn "  From flowing lines: enumerate 1-dose and 2-dose sets"
  putStrLn "  Score by Mirror-ID (cosine similarity) + tie-break"
  putStrLn ""

  let ctxs = [ emptySafetyCtx { bcPesticide = p
                              , bcTargetMatch = TM (countOverlap (targetVector p) exampleEntryVector) }
             | p <- pesticides ]
      lineResultsFull = [ runLineThroughBridges exampleEntryVector specBridges c | c <- ctxs ]
      flowing = [ p | (p, fr) <- zip pesticides lineResultsFull, not (isBlocked (frState fr)) ]
      pairSets = [ [a, b] | (i, a) <- zip [(0 :: Int) ..] flowing, b <- drop (i + 1) flowing ]
      candidates = map (: []) flowing ++ pairSets
      validSets = filter (not . setHasInternalMixingConflict) candidates
      scored = sortBy (comparing (Down . snd))
                 [ (s, scorePrescriptionSet s exampleEntryVector) | s <- validSets ]

  putStrLn $ "  Flowing pesticides: " ++ show (length flowing)
  putStrLn $ "  Valid prescription sets: " ++ show (length validSets)
  putStrLn ""
  putStrLn "  Top 5 prescriptions:"
  mapM_ (\(s, sc) -> do
      let names = intercalate "+" $ map pname s
      putStrLn $ "    " ++ padRight 40 names ++ " score=" ++ show sc
    ) (take 5 scored)

  -- ===== PROOF SUMMARY =====
  section "SUMMARY: Algebraic vs Procedural"
  putStrLn "  Original JS (procedural):"
  putStrLn "    if (usageCount >= max) return block();"
  putStrLn "    if (intervalDays < phi) return attenuate(0.5);"
  putStrLn "    if (toxicity == '劇物') return attenuate(0.7);"
  putStrLn ""
  putStrLn "  Haskell (algebraic):"
  putStrLn "    data WeightAction = FullPass | FullBlock | Attenuate Double"
  putStrLn "    weightFn :: BridgeContext -> WeightAction    -- pattern match"
  putStrLn ""
  putStrLn "  Engine loop is a pure fold:"
  putStrLn "    foldl' step initialFlow bridges"
  putStrLn "  Zero if/else in the engine. All branching is in the DATA."
  putStrLn ""
  putStrLn "  Structural invariants proven by types:"
  putStrLn "    - direction :: ForwardOnly    - no backward flow"
  putStrLn "    - level   :: Double           - strictly increasing (validated)"
  putStrLn "    - blocked :: FlowState        - algebraic, not boolean"
  putStrLn "    - weight  :: WeightAction     - three states, exhaustively matched"
  putStrLn ""
  putStrLn $ "  Real data loaded: " ++ show (length pesticides) ++ " pesticides, "
            ++ show (length evalBoxes) ++ " eval boxes"
  putStrLn ""
  putStrLn "  RBP business logic fully reimplemented in Haskell algebraic types."
  putStrLn "  Production data pipeline operational."
