/**
 * Accessibility context primitives — the context object, its types, and the
 * `useAccessibility` consumer hook.
 *
 * Kept separate from the `AccessibilityProvider` component (in
 * `AccessibilityContext.tsx`) so that the provider file only exports a
 * component, which keeps React Fast Refresh working.
 */
import { createContext, useContext } from 'react';

export interface AccessibilitySettings {
  highContrast: boolean;
  colorblindMode: 'none' | 'protanopia' | 'deuteranopia' | 'tritanopia';
  reducedMotion: boolean;
  fontSize: 'small' | 'medium' | 'large';
}

export interface AccessibilityContextType {
  settings: AccessibilitySettings;
  updateSettings: (newSettings: Partial<AccessibilitySettings>) => void;
  toggleHighContrast: () => void;
  toggleColorblindMode: () => void;
  toggleReducedMotion: () => void;
  setFontSize: (size: AccessibilitySettings['fontSize']) => void;
}

export const AccessibilityContext = createContext<AccessibilityContextType | undefined>(undefined);

export const useAccessibility = () => {
  const context = useContext(AccessibilityContext);
  if (!context) {
    throw new Error('useAccessibility must be used within an AccessibilityProvider');
  }
  return context;
};
