import { describe, it, expect } from 'vitest'
import { renderWithProviders } from '../_utils/render'
import { JDExtractingView } from '@/components/dashboard/jd-panels/JDExtractingView'

describe('JDExtractingView', () => {
  it('phase 1 (enrichment streaming): center shows enrichment skeleton, side panels show waiting placeholder', () => {
    const { getByTestId, queryByTestId } = renderWithProviders(
      <JDExtractingView
        descriptionRaw="raw text"
        enrichmentStatus="streaming"
        skipEnrichment={false}
      />,
    )
    expect(getByTestId('jd-center-loading-enrichment')).not.toBeNull()
    expect(queryByTestId('jd-side-panel-skeleton')).toBeNull()
    expect(getByTestId('jd-side-panel-waiting')).not.toBeNull()
  })

  it('phase 2 with enrichment ran: center shows enriched JD, side panels show signal-loading skeleton', () => {
    const { getByTestId, queryByTestId } = renderWithProviders(
      <JDExtractingView
        descriptionRaw="raw text"
        descriptionEnriched="enriched text"
        enrichmentStatus="completed"
        skipEnrichment={false}
      />,
    )
    expect(queryByTestId('jd-center-loading-enrichment')).toBeNull()
    expect(getByTestId('jd-center-enriched-body')).not.toBeNull()
    expect(getByTestId('jd-side-panel-skeleton')).not.toBeNull()
  })

  it('phase 2 with skip_enrichment=true: center shows raw JD, no enrichment phase visible', () => {
    const { getByTestId, queryByRole } = renderWithProviders(
      <JDExtractingView
        descriptionRaw="raw text"
        enrichmentStatus="idle"
        skipEnrichment={true}
      />,
    )
    expect(getByTestId('jd-center-raw-body')).not.toBeNull()
    expect(queryByRole('tab', { name: /Enriched JD/i })).toBeNull()
    expect(queryByRole('tab', { name: /Signal details/i })).toBeNull()
  })
})
