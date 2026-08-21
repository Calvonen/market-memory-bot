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

export function recordToText(record: Record<string, unknown>): string {
  return Object.entries(record)
    .map(([key, value]) => `${key}: ${value}`)
    .join('\n');
}

export function textToRecord(text: string): Record<string, number | string> {
  const record: Record<string, number | string> = {};
  for (const line of text.split('\n')) {
    const separatorIndex = line.indexOf(':');
    if (separatorIndex === -1) continue;
    const key = line.slice(0, separatorIndex).trim();
    const rawValue = line.slice(separatorIndex + 1).trim();
    if (!key || !rawValue) continue;
    const numeric = Number(rawValue);
    record[key] = Number.isFinite(numeric) && rawValue !== '' ? numeric : rawValue;
  }
  return record;
}

// Same "key: value per line" format as textToRecord, but for consensus
// specifically: recordToText() renders a null value as the literal text
// "key: null" (a JS template literal coerces null to that string), so the
// reverse parse must recognize that exact token and produce a real `null`
// back - not the *string* "null" (Number("null") is NaN, so without this
// special case the fallback branch below would silently store the string
// instead of round-tripping the actual null value).
export function textToConsensusRecord(text: string): Record<string, number | string | null> {
  const record: Record<string, number | string | null> = {};
  for (const line of text.split('\n')) {
    const separatorIndex = line.indexOf(':');
    if (separatorIndex === -1) continue;
    const key = line.slice(0, separatorIndex).trim();
    const rawValue = line.slice(separatorIndex + 1).trim();
    if (!key || !rawValue) continue;
    if (rawValue.toLowerCase() === 'null') {
      record[key] = null;
      continue;
    }
    const numeric = Number(rawValue);
    record[key] = Number.isFinite(numeric) ? numeric : rawValue;
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
    consensus: textToConsensusRecord(draft.consensusText),
    important_kpis: textToList(draft.kpiText),
    bull_case: textToList(draft.bullText),
    base_case: textToList(draft.baseText),
    bear_case: textToList(draft.bearText),
    triggers: textToRecord(draft.triggersText),
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
