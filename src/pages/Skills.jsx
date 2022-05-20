import Stack from "@mui/material/Stack";
import CardMedia from "@mui/material/CardMedia";
import { scale } from "react-animations/lib/utils";
import styled, { keyframes } from "styled-components";
import { fadeIn } from "react-animations";
import { useInView } from "react-intersection-observer";
import { skills } from "../data/skills";

const fadeInAnimation = keyframes`${fadeIn}`;
const FadeInDiv = styled.div`
  animation: 0.75s ${fadeInAnimation} ease-in;
`;

const Skill = ({ name }) => {
  return (
    <Stack direction="column">
      <CardMedia
        component="img"
        image={`/images/${name}.png`}
        alt="skillIcon"
        height="75em"
        sx={{
          transition: "all .1s linear",
          ":hover": { transform: scale(1.1) },
        }}
      />
      <p>{name}</p>
    </Stack>
  );
};

const Skills = () => {
  const { ref, inView, entry } = useInView({
    triggerOnce: true,
    threshold: 0.5,
  });

  return (
    <Stack
      direction={{ sm: "row", md: "column" }}
      justifyContent="center"
      height="100%"
      ref={ref}
    >
      <h1>Skills</h1>
      {inView && (
        <FadeInDiv>
          {skills.map((cat, idx) => (
            <Stack
              direction={{ sm: "column", md: "row" }}
              justifyContent="center"
              alignItems="center"
              spacing={3}
              margin={3}
              key={idx}
            >
              <h2>{cat.title}</h2>
              {cat.names.map((s, idx) => (
                <Skill name={s} key={idx} />
              ))}
            </Stack>
          ))}
        </FadeInDiv>
      )}
    </Stack>
  );
};

export default Skills;
