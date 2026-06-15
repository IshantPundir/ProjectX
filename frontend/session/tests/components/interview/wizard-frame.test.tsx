import { render, screen } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import { WizardFrame } from '@/app/interview/[token]/WizardFrame'

describe('WizardFrame', () => {
  it('renders the BinQle brand, the screening title, the progress indicator, and children', () => {
    render(
      <WizardFrame companyName="Acme" jobTitle="Engineer" steps={['Welcome', 'Ready']} currentIndex={0}>
        <div>stage body</div>
      </WizardFrame>,
    )
    expect(screen.getByRole('img', { name: /binqle\.ai/i })).toBeInTheDocument()
    expect(screen.getByText('Acme')).toBeInTheDocument()
    expect(screen.getByText(/step 1 of 2/i)).toBeInTheDocument()
    expect(screen.getByText('stage body')).toBeInTheDocument()
  })
})
