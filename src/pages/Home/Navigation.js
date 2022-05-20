import Stack from "@mui/material/Stack";
import Button from "@mui/material/Button";

const navData = ["About", "Projects", "Skills"];

const Navigation = ({ onClick }) => {
  return (
    <Stack spacing={2} direction="row" justifyContent="center" mt={5}>
      {navData.map((nav, idx) => (
        <Button variant="contained" id={nav.toLowerCase()} onClick={onClick} key={idx}>
          {nav}
        </Button>
      ))}
    </Stack>
  );
};

export default Navigation;
