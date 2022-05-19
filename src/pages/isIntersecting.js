import { useState, useEffect, useRef } from "react";

export default function useOnScreen(ref) {
  const [isOnScreen, setIsOnScreen] = useState(false);
  const observerRef = useRef(null);

  useEffect(() => {
    observerRef.current = new IntersectionObserver(([entry]) =>
      setIsOnScreen(entry.isIntersecting)
    );
  }, []);

  useEffect(() => {
    observerRef.current.observe(ref.current);
    // Remove the observer as soon as the component is unmounted
    return () => {
      observerRef.current.disconnect();
    };
  }, [ref]);

  return isOnScreen;
}
