'use client'

import { useSearchParams } from 'next/navigation'
import { BitcoinPortfolioTracker } from '../components/BitcoinPortfolioTracker'

export default function Home() {
  const searchParams = useSearchParams()
  const readOnly = searchParams.get('readonly') === 'true'

  return (
    <main className="flex min-h-screen flex-col items-center justify-between p-24">
      <title>Andrés Strategic Reserve</title>
      <BitcoinPortfolioTracker readOnly={readOnly} />
    </main>
  )
}

