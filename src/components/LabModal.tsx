import React, { useEffect, useMemo, useState } from 'react';
import { Card, CardContent, CardHeader } from '@/components/ui/card';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { X, Building2, User, Tag, Globe, GraduationCap } from 'lucide-react';
import { type LabInfo, type Researcher, type LabDetail, fetchLabDetail } from '@/services/dynamodb';
import GroundedDescription from './GroundedDescription';

interface LabModalProps {
  labId: string | null;
  labName: string | null;
  isOpen: boolean;
  onClose: () => void;
  labInfo: LabInfo | null;
  faculty: Researcher[];
  onClickResearcher: (researcherId: string) => void;
}

const LabModal: React.FC<LabModalProps> = ({
  labId,
  labName,
  isOpen,
  onClose,
  labInfo,
  faculty,
  onClickResearcher,
}) => {
  const [isClosing, setIsClosing] = useState(false);
  const [showFullDesc, setShowFullDesc] = useState(false);
  const [detail, setDetail] = useState<LabDetail | null>(null);

  // Fetch the Phase-4 enriched record (research areas / PI / homepage / grounded description).
  useEffect(() => {
    let active = true;
    setDetail(null);
    setShowFullDesc(false);
    if (labId) {
      fetchLabDetail(labId)
        .then((d) => active && setDetail(d))
        .catch(() => active && setDetail(null));
    }
    return () => {
      active = false;
    };
  }, [labId]);

  // Hooks must be called unconditionally and in the same order
  const description = detail?.description || labInfo?.description || '';
  const researchAreas = detail?.research_areas ?? [];
  const hasFaculty = faculty && faculty.length > 0;
  const preview = useMemo(() => {
    const MAX = 240;
    if (!description) return '';
    return description.length > MAX ? description.slice(0, MAX) + '…' : description;
  }, [description]);

  if (!isOpen || !labId) return null;

  const handleRequestClose = () => {
    if (isClosing) return;
    setIsClosing(true);
    setTimeout(() => {
      setIsClosing(false);
      onClose();
    }, 220);
  };

  return (
    <div className={`fixed inset-0 z-50 flex items-center justify-center transition-opacity duration-200 ${isClosing ? 'opacity-0' : 'opacity-100'}`}>
      <div className={`absolute inset-0 bg-black/50 backdrop-blur-sm transition-opacity duration-200 ${isClosing ? 'opacity-0' : 'opacity-100'}`} onClick={handleRequestClose} />
      <div className={`relative z-10 w-full max-w-2xl mx-4 transition-all duration-200 ${isClosing ? 'opacity-0 translate-y-4 scale-95' : 'opacity-100 translate-y-0 scale-100'}`}>
        <Card className="shadow-2xl border bg-card">
          <CardHeader className="pb-4 relative">
            <button
              onClick={handleRequestClose}
              className="absolute top-4 right-4 p-2 hover:bg-muted rounded-full"
              aria-label="Close"
            >
              <X className="w-5 h-5" />
            </button>
            <div className="flex items-center space-x-3 pr-10">
              <div className="w-10 h-10 rounded bg-primary flex items-center justify-center text-primary-foreground">
                <Building2 className="w-5 h-5" />
              </div>
              <div className="min-w-0">
                <h2 className="font-bold text-xl text-foreground truncate">{labName || labId}</h2>
                <Badge variant="outline" className="text-xs mt-1">{labId}</Badge>
              </div>
            </div>
          </CardHeader>
          <CardContent className="space-y-6 max-h-[60vh] overflow-y-auto">
            {description && (
              <div className="space-y-2">
                <div className="text-sm text-muted-foreground">Description</div>
                <p className="text-base text-foreground leading-relaxed">
                  {showFullDesc ? description : preview}
                  {description.length > preview.length && (
                    <Button variant="ghost" size="sm" className="h-6 px-2 text-xs ml-1 align-baseline text-muted-foreground hover:text-foreground" onClick={() => setShowFullDesc(v => !v)}>
                      {showFullDesc ? 'Show less' : 'Read more'}
                    </Button>
                  )}
                </p>
                <GroundedDescription nodeId={labId} inset={false} />
              </div>
            )}

            {detail?.pi && (
              <div className="flex items-center gap-2 text-base">
                <GraduationCap className="w-4 h-4 text-muted-foreground" />
                <span className="text-muted-foreground">PI</span>
                <span className="font-medium text-foreground">{detail.pi}</span>
              </div>
            )}

            {researchAreas.length > 0 && (
              <div>
                <div className="flex items-center gap-2 text-sm text-muted-foreground mb-2">
                  <Tag className="w-4 h-4" />
                  <span>Research areas</span>
                </div>
                <div className="flex flex-wrap gap-2">
                  {researchAreas.map((area, i) => (
                    <Badge key={i} variant="secondary" className="text-sm">{area}</Badge>
                  ))}
                </div>
              </div>
            )}

            {detail?.url && (
              <a
                href={detail.url}
                target="_blank"
                rel="noopener noreferrer"
                className="inline-flex items-center gap-2 text-sm text-primary hover:underline"
              >
                <Globe className="w-4 h-4" />
                Lab homepage
              </a>
            )}

            {hasFaculty && (
              <div>
                <div className="text-sm text-muted-foreground mb-2">Faculty</div>
                <div className="flex flex-wrap gap-2">
                  {faculty.map(f => (
                    <button
                      key={f.id}
                      onClick={() => onClickResearcher(f.id)}
                      className="inline-flex items-center gap-2 px-2.5 py-1.5 rounded-full border hover:bg-muted transition-colors text-sm"
                    >
                      <span className="w-6 h-6 rounded-full bg-primary/10 text-primary flex items-center justify-center">
                        <User className="w-3.5 h-3.5" />
                      </span>
                      <span className="font-medium">{f.name}</span>
                    </button>
                  ))}
                </div>
              </div>
            )}
          </CardContent>
        </Card>
      </div>
    </div>
  );
};

export default LabModal;


