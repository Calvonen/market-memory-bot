// Shared text <-> structured-field conversion for the strategy draft flow.
// The mobile UI edits lists/records as one-entry-per-line text (same
// convention as the older raw field editor) so this stays a single place
// that both the draft editor and the summary/confirm screens rely on.

import { EventExpectation, StrategyDraftInput } from '@/services/api';

export type DraftFormState = {
  instrument: string;
  eventName: string;
  scheduledDate: string;
  consensusText: string;
  kpiText: string;
  bullText: string;
  baseText: string;
  bearText: string;
  triggersText: string;
  invalidationText: string;
  sourceName: string;
  sourceUrl: string;
  sourceAsOf: string;
  changeNote: string;
  summary: string;
  assumptionsText: string;
  unresolvedText: string;
};

export function listToText(items: string[]): string {
  return items.join('\n');
}

export function textToList(text: string): string[] {
  return text
    .split('\n')
    .map((line) => line.trim())
    .filter((line) => line.length > 0);
}

// Values are always rendered as a JSON literal, never a bare
// `${value}` interpolation - that's what makes the round trip through
// textToTypedRecord() below lossless and unambiguous. A number renders bare
// (`1`, `55.6`), `null` renders bare (`null`), and a string always renders
// *quoted* (`"001"`, `"null"`, `"manual"`) - so the string "001" is never
// confusable with the number 1, and the string "null" is never confusable
// with real null, purely from the text alone.
export function recordToText(record: Record<string, unknown>): string {
  return Object.entries(record)
    .map(([key, value]) => `${key}: ${JSON.stringify(value)}`)
    .join('\n');
}

// Parses one "key: value" line's value as a JSON scalar - the only
// unambiguous way to tell the number 1 from the string "1", the string
// "001" from the number 1 (leading zeros make "001" invalid JSON number
// syntax, so it falls through to the literal-string fallback below rather
// than ever being coerced), and real `null` from the three-character
// string "null" (which only round-trips back to that exact string because
// recordToText() always JSON.stringifies values, quoting every string).
// No heuristic "does this look numeric" guessing anywhere in this function.
function parseTypedScalar(rawValue: string): number | string | null {
  try {
    const parsed: unknown = JSON.parse(rawValue);
    if (parsed === null || typeof parsed === 'number' || typeof parsed === 'string') {
      return parsed;
    }
    // A boolean, array, or object isn't a value type consensus/triggers
    // use - fall through to the literal-string fallback below rather than
    // silently coercing or dropping it.
  } catch {
    // Not valid JSON (e.g. a bare, unquoted word, or "001") - fall through
    // to the literal-string fallback below.
  }
  return rawValue;
}

// Shared by both consensus and triggers: same "key: value per line" text
// format, same lossless JSON-scalar parsing per value.
export function textToTypedRecord(text: string): Record<string, number | string | null> {
  const record: Record<string, number | string | null> = {};
  for (const line of text.split('\n')) {
    const separatorIndex = line.indexOf(':');
    if (separatorIndex === -1) continue;
    const key = line.slice(0, separatorIndex).trim();
    const rawValue = line.slice(separatorIndex + 1).trim();
    if (!key || !rawValue) continue;
    record[key] = parseTypedScalar(rawValue);
  }
  return record;
}

export function draftFormFromEvent(event: EventExpectation): DraftFormState {
  return {
    instrument: event.instrument,
    eventName: event.event_name,
    scheduledDate: event.scheduled_date,
    consensusText: recordToText(event.consensus),
    kpiText: listToText(event.important_kpis),
    bullText: listToText(event.bull_case),
    baseText: listToText(event.base_case),
    bearText: listToText(event.bear_case),
    triggersText: recordToText(event.triggers),
    invalidationText: listToText(event.invalidation_conditions),
    sourceName: event.source_name ?? '',
    sourceUrl: event.source_url ?? '',
    sourceAsOf: event.source_as_of ?? '',
    changeNote: '',
    summary: '',
    assumptionsText: '',
    unresolvedText: '',
  };
}

export function draftFormToInput(draft: DraftFormState): StrategyDraftInput {
  return {
    instrument: draft.instrument,
    event_name: draft.eventName,
    scheduled_date: draft.scheduledDate,
    consensus: textToTypedRecord(draft.consensusText),
    important_kpis: textToList(draft.kpiText),
    bull_case: textToList(draft.bullText),
    base_case: textToList(draft.baseText),
    bear_case: textToList(draft.bearText),
    triggers: textToTypedRecord(draft.triggersText),
    invalidation_conditions: textToList(draft.invalidationText),
    source_name: draft.sourceName.trim() || null,
    source_url: draft.sourceUrl.trim() || null,
    source_as_of: draft.sourceAsOf.trim() || null,
    change_note: draft.changeNote,
    summary: draft.summary,
    assumptions: textToList(draft.assumptionsText),
    unresolved_questions: textToList(draft.unresolvedText),
  };
}
