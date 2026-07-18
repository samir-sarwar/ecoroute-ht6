import { SectionView } from "../../components/SectionView";

export default async function SectionPage({ params }: { params: Promise<{ section: string }> }) {
  const { section } = await params;
  return <SectionView section={section} />;
}

